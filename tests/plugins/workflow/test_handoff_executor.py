from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

import pytest

from agent.structured_output import StructuredOutputStrategy, normalize_schema
from hermes_cli.handoff import (
    AdvanceResult,
    AgentHandoffService,
    ChannelObservation,
    HandoffEndpoint,
    HandoffSnapshot,
    HandoffSpec,
    HandoffStore,
)
from hermes_cli.runtime_provider import StructuredOutputCapabilityDecision
from plugins.workflow.executors.base import NodeExecutionContext
from plugins.workflow.executors.handoff import HandoffPromptExecutor
from plugins.workflow.models import (
    WorkflowLanguageProfile,
    WorkflowNode,
    WorkflowStructuredOutput,
    freeze_value,
)
from plugins.workflow.resources import VariableContext


NOW = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)


def _node(**options: object) -> WorkflowNode:
    return WorkflowNode(
        id="review",
        node_type="prompt",
        value="Review $ARGUMENTS",
        depends_on=(),
        source_index=0,
        source_line=1,
        options=freeze_value(options),
    )


def _context(
    tmp_path: Path,
    *,
    node: WorkflowNode | None = None,
    node_state: dict[str, object] | None = None,
    **kwargs: object,
) -> NodeExecutionContext:
    run_directory = tmp_path / "run"
    run_directory.mkdir(parents=True, exist_ok=True)
    return NodeExecutionContext(
        run_id="run-1",
        run_directory=run_directory,
        node=node or _node(),
        attempt_id="attempt-1",
        variable_context=VariableContext(arguments="evidence", workflow_id="run-1"),
        node_state=node_state or {},
        **kwargs,
    )


def _spec(*, output_schema=None) -> HandoffSpec:
    return HandoffSpec(
        mode="task",
        endpoint=HandoffEndpoint.parse("hermes://local/reviewer"),
        prompt="Review evidence",
        output_schema=output_schema,
        deadline_at=NOW + timedelta(hours=4),
        attribution={"consumer": "workflow", "run": "run-1", "node": "review"},
        required_capabilities=(
            frozenset({"structured_output", "cancellation"})
            if output_schema is not None
            else frozenset({"cancellation"})
        ),
    )


def _snapshot(
    phase: str,
    *,
    handoff_id: str = "handoff-1",
    version: int = 2,
    text: str | None = None,
    failure_code: str | None = None,
    output_schema=None,
) -> HandoffSnapshot:
    terminal_result = None
    if text is not None:
        encoded = text.encode()
        terminal_result = {
            "text": text,
            "sha256": sha256(encoded).hexdigest(),
            "media_type": "application/json" if output_schema else "text/plain",
            "size_bytes": len(encoded),
        }
    return HandoffSnapshot(
        handoff_id=handoff_id,
        key_scope="default",
        handoff_key="run-1:review:1",
        spec=_spec(output_schema=output_schema),
        spec_fingerprint=_spec(output_schema=output_schema).fingerprint,
        phase=phase,
        state_version=version,
        next_advance_at=NOW + timedelta(seconds=5),
        terminal_result=terminal_result,
        failure_code=failure_code,
        created_at=NOW,
        updated_at=NOW,
    )


class FakeService:
    def __init__(self, snapshot: HandoffSnapshot) -> None:
        self.snapshot = snapshot
        self.create_calls: list[tuple[HandoffSpec, str, str]] = []
        self.advance_calls: list[str] = []
        self.get_calls: list[str] = []

    def create(self, spec, initiator, *, handoff_key):
        self.create_calls.append((spec, initiator, handoff_key))
        return self.snapshot

    def advance(self, handoff_id, *, budget_seconds=2.0):
        self.advance_calls.append(handoff_id)
        return AdvanceResult(self.snapshot, "observe", True)

    def get(self, handoff_id):
        self.get_calls.append(handoff_id)
        return self.snapshot


def _executor(
    service: FakeService,
    *,
    interaction_policy: str = "deny",
    endpoint: str = "hermes://local/reviewer",
) -> HandoffPromptExecutor:
    return HandoffPromptExecutor(
        service,
        {
            "endpoint": endpoint,
            "interaction_policy": interaction_policy,
            "on_deadline": "cancel_and_fail",
        },
        initiator_profile="default",
        deadline_at=NOW + timedelta(hours=4),
        utcnow=lambda: NOW,
    )


def _wait_state(phase: str = "active") -> dict[str, object]:
    return {
        "state": "ready",
        "retry_consumed": 0,
        "handoff": {
            "handoff_id": "handoff-1",
            "generation": 1,
            "last_observed_version": 2,
            "last_observed_phase": phase,
            "next_observation_at": (NOW + timedelta(seconds=5)).isoformat(),
            "deadline_at": (NOW + timedelta(hours=4)).isoformat(),
        },
        "attempts": [{"attempt_id": "attempt-1", "state": "claimed"}],
    }


def test_first_dispatch_uses_stable_semantic_key_and_one_bounded_advance(tmp_path):
    service = FakeService(_snapshot("active"))
    executor = _executor(service)

    first = executor.execute(_context(tmp_path))
    replay = executor.execute(_context(tmp_path))

    assert first.status == replay.status == "waiting_handoff"
    assert [call[2] for call in service.create_calls] == [
        "run-1:review:1",
        "run-1:review:1",
    ]
    assert service.create_calls[0][0].fingerprint == service.create_calls[1][0].fingerprint
    assert service.create_calls[0][0].prompt == "Review evidence"
    assert service.advance_calls == ["handoff-1", "handoff-1"]
    assert first.metadata == {
        "handoff_id": "handoff-1",
        "handoff_generation": 1,
        "handoff_observed_version": 2,
        "handoff_observed_phase": "active",
        "handoff_next_observation_at": "2026-09-01T12:00:05+00:00",
        "handoff_deadline_at": "2026-09-01T16:00:00+00:00",
        "known_no_effect": True,
        "provider_attempts": 0,
        "provider_attempts_exact": True,
    }


@pytest.mark.parametrize(
    ("interaction_policy", "expected_capabilities"),
    [
        ("pause", {"approval", "cancellation"}),
        ("deny", {"cancellation"}),
        ("auto_cancel", {"cancellation"}),
    ],
)
def test_peer_policy_sets_exact_immutable_spec_capabilities(
    tmp_path, interaction_policy, expected_capabilities
):
    service = FakeService(_snapshot("active"))

    result = _executor(
        service,
        interaction_policy=interaction_policy,
        endpoint="hermes://peer/office/reviewer",
    ).execute(_context(tmp_path))

    spec, initiator, handoff_key = service.create_calls[0]
    assert result.status == "waiting_handoff"
    assert spec.endpoint.canonical == "hermes://peer/office/reviewer"
    assert spec.prompt == "Review evidence"
    assert spec.required_capabilities == frozenset(expected_capabilities)
    assert (initiator, handoff_key) == ("default", "run-1:review:1")


def test_structured_output_is_not_a_required_peer_capability(tmp_path):
    schema = {"type": "object", "required": ["answer"]}
    service = FakeService(_snapshot("active", output_schema=schema))

    _executor(
        service,
        endpoint="hermes://peer/office/reviewer",
    ).execute(_context(tmp_path, node=_node(output_format=schema)))

    assert service.create_calls[0][0].required_capabilities == frozenset({
        "cancellation",
        "structured_output",
    })


def test_create_replay_uses_one_durable_handoff_and_one_channel_step_per_turn(
    tmp_path,
):
    class Channel:
        def __init__(self):
            self.calls = []

        def bind(self, snapshot, *, budget_seconds):
            self.calls.append(("bind", snapshot.handoff_id))
            return ChannelObservation(
                phase="prepared",
                mechanism="fake",
                binding={"profile": "reviewer", "mechanism": "fake"},
            )

        def submit(self, snapshot, *, budget_seconds):
            self.calls.append(("submit", snapshot.handoff_id))
            return ChannelObservation(
                phase="submitted",
                next_advance_at=NOW + timedelta(seconds=5),
            )

    channel = Channel()
    service = AgentHandoffService(HandoffStore(tmp_path / "handoffs.db"), channel)
    executor = _executor(service)

    first = executor.execute(_context(tmp_path))
    replay = executor.execute(_context(tmp_path))

    assert first.metadata["handoff_id"] == replay.metadata["handoff_id"]
    assert len(service.list({}, limit=10)) == 1
    assert channel.calls == [
        ("bind", first.metadata["handoff_id"]),
        ("submit", first.metadata["handoff_id"]),
    ]


def test_resumption_reads_the_exact_handoff_without_creating_or_advancing(tmp_path):
    service = FakeService(_snapshot("active"))

    result = _executor(service).execute(
        _context(tmp_path, node_state=_wait_state())
    )

    assert result.status == "waiting_handoff"
    assert service.get_calls == ["handoff-1"]
    assert service.create_calls == []
    assert service.advance_calls == []


def test_resumption_rejects_a_handoff_with_mismatched_identity(tmp_path):
    service = FakeService(
        replace(_snapshot("active"), handoff_key="run-1:review:other")
    )

    result = _executor(service).execute(
        _context(tmp_path, node_state=_wait_state())
    )

    assert result.status == "failed"
    assert result.error_code == "handoff_identity_mismatch"
    assert service.get_calls == ["handoff-1"]
    assert service.create_calls == []
    assert service.advance_calls == []


def test_terminal_success_reuses_ordinary_structured_output_validation(tmp_path):
    schema = {
        "type": "object",
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
    }
    service = FakeService(_snapshot("succeeded", text='{"answer":"ok"}', output_schema=schema))

    result = _executor(service).execute(
        _context(tmp_path, node=_node(output_format=schema), node_state=_wait_state("succeeded"))
    )

    assert result.status == "succeeded"
    assert result.error_code is None
    assert result.metadata["output"] == '{"answer":"ok"}'
    assert result.metadata["handoff_id"] == "handoff-1"
    output = tmp_path / "run" / result.artifacts[0].relative_path
    assert output.read_bytes() == b'{"answer":"ok"}'


def test_terminal_result_on_first_advance_is_returned_as_internal_wait(tmp_path):
    schema = {"type": "object", "required": ["answer"]}
    service = FakeService(
        _snapshot("succeeded", text='{"answer":"ok"}', output_schema=schema)
    )

    result = _executor(service).execute(
        _context(tmp_path, node=_node(output_format=schema))
    )

    assert result.status == "waiting_handoff"
    assert result.metadata["handoff_observed_phase"] == "succeeded"
    assert result.metadata["handoff_generation"] == 1
    assert not list((tmp_path / "run").glob("nodes/**/output.json"))


def test_archon_terminal_success_produces_the_canonical_primary_output(tmp_path):
    schema = {
        "type": "object",
        "required": ["answer"],
        "properties": {"answer": {"type": "string"}},
    }
    normalized = normalize_schema(schema)
    service = FakeService(
        _snapshot("succeeded", text=' { "answer" : "ok" } ', output_schema=schema)
    )
    context = _context(
        tmp_path,
        node=_node(output_format=schema, output_type="review-result"),
        node_state=_wait_state("succeeded"),
        language_profile=WorkflowLanguageProfile.ARCHON_2026_07,
        structured_output=WorkflowStructuredOutput(
            canonical_schema=normalized.canonical_schema,
            schema_fingerprint=normalized.schema_fingerprint,
        ),
        structured_output_decision=StructuredOutputCapabilityDecision(
            strategy=StructuredOutputStrategy.PROMPT_JSON_SCHEMA,
            effective_provider="fake-provider",
            model="fake-model",
            api_mode="chat_completions",
            declaration_source="test",
            adapter_version=1,
            schema_fingerprint=normalized.schema_fingerprint,
            rationale="test",
        ),
    )

    result = _executor(service).execute(context)

    assert result.status == "succeeded"
    assert result.primary_output is not None
    assert result.primary_output.structured_value == {"answer": "ok"}
    assert result.primary_output.output_type == "review-result"
    output = tmp_path / "run" / result.primary_output.attempt_relative_path
    assert output.read_bytes() == b'{"answer":"ok"}'


def test_invalid_external_output_uses_existing_failure_code(tmp_path):
    schema = {"type": "object", "required": ["answer"]}
    service = FakeService(_snapshot("succeeded", text="not-json", output_schema=schema))

    result = _executor(service).execute(
        _context(tmp_path, node=_node(output_format=schema), node_state=_wait_state("succeeded"))
    )

    assert result.status == "failed"
    assert result.error_code == "structured_output_invalid"
    assert not result.artifacts
    assert result.metadata["handoff_id"] == "handoff-1"
    assert result.metadata["handoff_observed_phase"] == "succeeded"


@pytest.mark.parametrize(
    ("phase", "failure_code", "expected_code"),
    [
        ("failed", "provider_failed", "handoff_remote_failed"),
        ("cancelled", None, "handoff_remote_cancelled"),
    ],
)
def test_definitive_remote_terminal_outcomes_are_ordinary_failures(
    tmp_path, phase, failure_code, expected_code
):
    service = FakeService(_snapshot(phase, failure_code=failure_code))

    result = _executor(service).execute(
        _context(tmp_path, node_state=_wait_state(phase))
    )

    assert result.status == "failed"
    assert result.error_code == expected_code
    assert result.metadata["handoff_failure_code"] == failure_code


def test_indeterminate_remains_a_reconciliation_wait(tmp_path):
    service = FakeService(_snapshot("indeterminate", failure_code="observation_indeterminate"))

    result = _executor(service).execute(
        _context(tmp_path, node_state=_wait_state("indeterminate"))
    )

    assert result.status == "waiting_handoff"
    assert result.metadata["handoff_observed_phase"] == "indeterminate"
    assert result.metadata["known_no_effect"] is True
    assert service.create_calls == []
    assert service.advance_calls == []


def test_definitive_retry_uses_the_next_semantic_generation(tmp_path):
    snapshot = replace(
        _snapshot("active", handoff_id="handoff-2"),
        handoff_key="run-1:review:2",
    )
    service = FakeService(snapshot)
    state = _wait_state("failed")
    state["retry_consumed"] = 1
    # Scheduler captures node_state before adding the current worker claim.
    state["attempts"] = [
        {"attempt_id": "attempt-1", "state": "waiting_handoff"},
        {"attempt_id": "attempt-2", "state": "failed"},
    ]

    result = _executor(service).execute(_context(tmp_path, node_state=state))

    assert result.status == "waiting_handoff"
    assert service.create_calls[0][2] == "run-1:review:2"
    assert result.metadata["handoff_generation"] == 2


def test_local_acceptance_failure_after_remote_success_starts_next_generation(
    tmp_path,
):
    snapshot = replace(
        _snapshot("active", handoff_id="handoff-2"),
        handoff_key="run-1:review:2",
    )
    service = FakeService(snapshot)
    state = _wait_state("succeeded")
    state["retry_consumed"] = 1
    state["attempts"] = [
        {"attempt_id": "attempt-1", "state": "waiting_handoff"},
        {
            "attempt_id": "attempt-2",
            "state": "failed",
            "error_code": "structured_output_invalid",
        },
    ]

    result = _executor(service).execute(_context(tmp_path, node_state=state))

    assert result.status == "waiting_handoff"
    assert [call[2] for call in service.create_calls] == ["run-1:review:2"]
    assert service.get_calls == []
    assert result.metadata["handoff_generation"] == 2
