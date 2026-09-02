from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import MappingProxyType

import pytest

from hermes_cli.handoff.models import (
    ChannelObservation,
    HandoffEndpoint,
    HandoffSnapshot,
    HandoffSpec,
)


def _endpoint() -> HandoffEndpoint:
    return HandoffEndpoint.parse("hermes://local/reviewer")


def _spec(**changes: object) -> HandoffSpec:
    values = {
        "mode": "task",
        "endpoint": _endpoint(),
        "prompt": "Review this change.",
        "output_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
        "deadline_at": datetime(2026, 9, 1, 12, 30, tzinfo=timezone.utc),
        "attribution": {"workflow": "release-check", "node": "review"},
        "required_capabilities": {"structured_output", "cancellation"},
    }
    values.update(changes)
    return HandoffSpec(**values)


def test_endpoint_parses_only_canonical_local_profile_uri():
    endpoint = _endpoint()

    assert endpoint.kind == "local"
    assert endpoint.peer is None
    assert endpoint.profile == "reviewer"
    assert endpoint.canonical == "hermes://local/reviewer"


def test_endpoint_parses_only_canonical_registered_peer_profile_uri():
    endpoint = HandoffEndpoint.parse("hermes://peer/review-host/qa_team")

    assert endpoint.kind == "peer"
    assert endpoint.peer == "review-host"
    assert endpoint.profile == "qa_team"
    assert endpoint.canonical == "hermes://peer/review-host/qa_team"


@pytest.mark.parametrize("value", [
    "hermes://local",
    "hermes://local/",
    "hermes://local/reviewer/extra",
    "hermes://user@local/reviewer",
    "hermes://user:password@local/reviewer",
    "hermes://peer/reviewer",
    "hermes://local:8080/reviewer",
    "hermes://local/reviewer?x=1",
    "hermes://local/reviewer?",
    "hermes://local/reviewer#fragment",
    "hermes://local/reviewer#",
    "hermes://local/reviewer%2Fextra",
    "hermes://local/review%00er",
    "hermes://local/Reviewer",
    "hermes://local/reviewer%20",
    "hermes://peer",
    "hermes://peer/",
    "hermes://peer/reviewer/",
    "hermes://peer/Reviewer/qa",
    "hermes://peer/reviewer/QA",
    "hermes://peer/reviewer.example/qa",
    "hermes://peer/reviewer/qa/extra",
    "hermes://user@peer/reviewer/qa",
    "hermes://peer:8080/reviewer/qa",
    "hermes://peer/reviewer/qa?x=1",
    "hermes://peer/reviewer/qa#fragment",
    "hermes://peer/reviewer%2Fqa",
    f"hermes://peer/{'a' * 65}/qa",
])
def test_endpoint_rejects_every_noncanonical_or_unsafe_form(value: str):
    with pytest.raises(ValueError):
        HandoffEndpoint.parse(value)


def test_spec_normalizes_aware_deadline_and_freezes_semantic_values():
    source_schema = {"properties": {"answer": {"type": "string"}}, "type": "object"}
    source_attribution = {"node": "review", "workflow": "release-check"}
    spec = _spec(
        output_schema=source_schema,
        deadline_at=datetime(2026, 9, 1, 8, 30, tzinfo=timezone(timedelta(hours=-4))),
        attribution=source_attribution,
    )

    source_schema["type"] = "array"
    source_attribution["node"] = "changed"

    assert spec.deadline_at == datetime(2026, 9, 1, 12, 30, tzinfo=timezone.utc)
    assert isinstance(spec.output_schema, MappingProxyType)
    assert isinstance(spec.attribution, MappingProxyType)
    assert spec.output_schema["type"] == "object"
    assert spec.attribution["node"] == "review"
    with pytest.raises(TypeError):
        spec.attribution["node"] = "changed"  # type: ignore[index]


@pytest.mark.parametrize("changes", [
    {"mode": "conversation"},
    {"prompt": "   \n\t"},
    {"prompt": "x" * 500_001},
    {"deadline_at": datetime(2026, 9, 1, 12, 30)},
    {"attribution": {"api_token": "redacted"}},
    {"attribution": {"source": "Bearer abc"}},
    {"attribution": {"source": "https://example.test/path"}},
    {"attribution": {"source": "mailto:reviewer@example.test"}},
    {"attribution": {"source": "//example.test/path"}},
    {"attribution": {"source": "line\nbreak"}},
    {"required_capabilities": {"remote_execution"}},
])
def test_spec_rejects_invalid_task_contract_inputs(changes: dict[str, object]):
    with pytest.raises(ValueError):
        _spec(**changes)


def test_spec_accepts_closed_stage_two_capabilities():
    spec = _spec(
        required_capabilities={
            "approval",
            "cancellation",
            "follow_up",
            "steering",
            "structured_output",
        }
    )

    assert spec.required_capabilities == frozenset({
        "approval",
        "cancellation",
        "follow_up",
        "steering",
        "structured_output",
    })


def test_spec_reuses_bounded_structured_output_normalization():
    properties = {str(index): {"type": "string"} for index in range(1_025)}

    with pytest.raises(ValueError):
        _spec(output_schema={"type": "object", "properties": properties})


def test_spec_fingerprint_input_is_stable_canonical_json_of_semantics():
    first = _spec(
        output_schema={"properties": {"answer": {"type": "string"}}, "type": "object"},
        attribution={"workflow": "release-check", "node": "review"},
        required_capabilities={"cancellation", "structured_output"},
    )
    second = _spec(
        output_schema={"type": "object", "properties": {"answer": {"type": "string"}}},
        attribution={"node": "review", "workflow": "release-check"},
        required_capabilities={"structured_output", "cancellation"},
    )

    assert first.fingerprint_input == second.fingerprint_input
    assert first.fingerprint == second.fingerprint
    assert first.fingerprint == sha256(first.fingerprint_input).hexdigest()
    assert first.fingerprint_input == (
        b'{"attribution":{"node":"review","workflow":"release-check"},'
        b'"deadline_at":"2026-09-01T12:30:00.000000Z",'
        b'"endpoint":"hermes://local/reviewer","mode":"task",'
        b'"output_schema":{"$schema":"https://json-schema.org/draft/2020-12/schema",'
        b'"properties":{"answer":{"type":"string"}},"type":"object"},'
        b'"prompt":"Review this change.",'
        b'"required_capabilities":["cancellation","structured_output"]}'
    )


def test_snapshot_validates_lifecycle_metadata_and_freezes_mappings():
    binding = {"profile": "reviewer", "mechanism": "runs"}
    checkpoint = {"run_id": "run-1", "cursor": 1}
    snapshot = HandoffSnapshot(
        handoff_id="handoff-1",
        key_scope="workflow/run-1",
        handoff_key="node/review",
        spec=_spec(),
        spec_fingerprint="a" * 64,
        phase="active",
        state_version=2,
        binding=binding,
        checkpoint=checkpoint,
        created_at=datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
    )

    binding["profile"] = "changed"
    checkpoint["cursor"] = 2

    assert snapshot.binding["profile"] == "reviewer"
    assert snapshot.checkpoint["cursor"] == 1
    with pytest.raises(ValueError):
        HandoffSnapshot("id", "scope", "key", _spec(), "f", "unknown", 0)
    with pytest.raises(ValueError):
        HandoffSnapshot("id", "scope", "key", _spec(), "f", "prepared", -1)
    with pytest.raises(ValueError):
        HandoffSnapshot(
            "id", "scope", "key", _spec(), "f", "prepared", 0,
            created_at=datetime(2026, 9, 1, 12),
        )


def test_channel_observation_uses_closed_immutable_facts():
    checkpoint = {"run_id": "run-1"}
    observation = ChannelObservation(
        phase="active",
        checkpoint=checkpoint,
        binding={"profile": "reviewer", "mechanism": "runs"},
        failure_code="transient_failure",
        next_advance_at=datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
    )

    checkpoint["run_id"] = "changed"

    assert observation.checkpoint["run_id"] == "run-1"
    assert observation.binding["profile"] == "reviewer"
    with pytest.raises(ValueError):
        ChannelObservation(phase="unknown")
    with pytest.raises(ValueError):
        ChannelObservation(phase="active", next_advance_at=datetime(2026, 9, 1, 12))
    with pytest.raises(TypeError):
        ChannelObservation(phase="active", safe_data={})


@pytest.mark.parametrize("field, value", [
    ("binding", {"opaque": "provider error: unredacted"}),
    ("checkpoint", {"opaque": {"value": "Bearer secret"}}),
    ("checkpoint", {"opaque": {"value": "/Users/example/.hermes"}}),
    ("checkpoint", {"run_id": "run/unsafe"}),
    ("checkpoint", {"request_sha256": "A" * 64}),
    ("checkpoint", {"process_pid": True}),
    ("checkpoint", {"process_started_at": -1}),
])
def test_snapshot_rejects_noncontract_durable_facts(field: str, value: dict[str, object]):
    values = {field: value}

    with pytest.raises(ValueError):
        HandoffSnapshot(
            "id", "scope", "key", _spec(), "f", "prepared", 0, **values,
        )


@pytest.mark.parametrize("result", [
    {"text": "answer", "sha256": "0" * 64, "media_type": "text/plain", "size_bytes": 6},
    {"text": "answer", "sha256": sha256(b"answer").hexdigest(), "media_type": "text/html", "size_bytes": 6},
    {"text": "answer", "sha256": sha256(b"answer").hexdigest(), "media_type": "text/plain", "size_bytes": 5},
    {"text": "answer", "sha256": sha256(b"answer").hexdigest(), "media_type": "text/plain", "size_bytes": 6, "detail": "provider error"},
])
def test_observation_rejects_malformed_result_integrity(result: dict[str, object]):
    with pytest.raises(ValueError):
        ChannelObservation(phase="succeeded", terminal_result=result)


def test_fact_failure_code_must_be_a_safe_identifier():
    with pytest.raises(ValueError):
        ChannelObservation(phase="failed", failure_code="raw provider error")


@pytest.mark.parametrize("mechanism", [
    "Bearer secret",
    "/Users/example/.hermes",
    "raw header error prose",
])
def test_top_level_mechanism_must_be_a_safe_identifier(mechanism: str):
    with pytest.raises(ValueError):
        HandoffSnapshot("id", "scope", "key", _spec(), "f", "prepared", 0, mechanism=mechanism)
    with pytest.raises(ValueError):
        ChannelObservation(phase="prepared", mechanism=mechanism)


def test_snapshot_and_observation_accept_closed_stage_one_facts_immutably():
    result = {
        "text": "answer",
        "sha256": sha256(b"answer").hexdigest(),
        "media_type": "text/plain",
        "size_bytes": 6,
    }
    snapshot = HandoffSnapshot(
        "id", "scope", "key", _spec(), "f", "succeeded", 1,
        binding={"profile": "reviewer", "mechanism": "runs"},
        checkpoint={"run_id": "run-1", "request_sha256": "a" * 64, "process_pid": 12},
        terminal_result=result,
        failure_code="none",
    )
    observation = ChannelObservation(
        phase="succeeded",
        binding={"profile": "reviewer", "mechanism": "runs"},
        checkpoint={"status": "completed", "version": 1},
        terminal_result=result,
        failure_code="none",
    )

    result["text"] = "changed"

    assert snapshot.terminal_result["text"] == "answer"
    assert observation.terminal_result["text"] == "answer"


def test_snapshot_accepts_closed_peer_binding_and_pending_approval_facts():
    binding = {
        "peer": "review-host",
        "profile": "qa_team",
        "mechanism": "peer_runs",
        "capabilities": [
            "steering",
            "durable_admission",
            "approval",
            "authoritative_status",
            "cancellation",
            "follow_up",
        ],
        "origin_sha256": "a" * 64,
        "auth_scope_sha256": "b" * 64,
    }
    checkpoint = {
        "run_id": "run-1",
        "approval_request_id": "approval-1",
        "approval_choices": ["deny", "once", "always", "session"],
    }
    snapshot = HandoffSnapshot(
        "id",
        "scope",
        "key",
        _spec(endpoint=HandoffEndpoint.parse("hermes://peer/review-host/qa_team")),
        "f",
        "needs_input",
        1,
        mechanism="peer_runs",
        binding=binding,
        checkpoint=checkpoint,
    )

    binding["peer"] = "changed"
    checkpoint["approval_request_id"] = "changed"

    assert snapshot.binding == {
        "auth_scope_sha256": "b" * 64,
        "capabilities": (
            "approval",
            "authoritative_status",
            "cancellation",
            "durable_admission",
            "follow_up",
            "steering",
        ),
        "mechanism": "peer_runs",
        "origin_sha256": "a" * 64,
        "peer": "review-host",
        "profile": "qa_team",
    }
    assert snapshot.checkpoint["approval_request_id"] == "approval-1"
    assert snapshot.checkpoint["approval_choices"] == (
        "once",
        "session",
        "always",
        "deny",
    )


@pytest.mark.parametrize(
    "field,value",
    [
        (
            "binding",
            {
                "peer": "Review-Host",
                "profile": "qa_team",
                "mechanism": "peer_runs",
                "capabilities": ["durable_admission", "authoritative_status"],
                "origin_sha256": "a" * 64,
                "auth_scope_sha256": "b" * 64,
            },
        ),
        (
            "binding",
            {
                "peer": "review-host",
                "profile": "qa_team",
                "mechanism": "peer_runs",
                "capabilities": ["raw_remote_shell"],
                "origin_sha256": "a" * 64,
                "auth_scope_sha256": "b" * 64,
            },
        ),
        (
            "binding",
            {
                "peer": "review-host",
                "profile": "qa_team",
                "mechanism": "peer_runs",
                "capabilities": ["durable_admission", "authoritative_status"],
                "origin_sha256": "A" * 64,
                "auth_scope_sha256": "b" * 64,
            },
        ),
        (
            "checkpoint",
            {
                "approval_request_id": "approval-1",
            },
        ),
        (
            "checkpoint",
            {
                "approval_request_id": "approval-1",
                "approval_choices": ["once", "ask"],
            },
        ),
    ],
)
def test_snapshot_rejects_noncontract_peer_facts(
    field: str, value: dict[str, object]
):
    with pytest.raises(ValueError):
        HandoffSnapshot(
            "id",
            "scope",
            "key",
            _spec(),
            "f",
            "prepared",
            0,
            **{field: value},
        )
