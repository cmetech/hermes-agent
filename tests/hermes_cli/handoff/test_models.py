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

    assert endpoint.profile == "reviewer"
    assert endpoint.canonical == "hermes://local/reviewer"


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
    binding = {"run_id": "run-1"}
    checkpoint = {"cursor": "1"}
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

    binding["run_id"] = "changed"
    checkpoint["cursor"] = "2"

    assert snapshot.binding["run_id"] == "run-1"
    assert snapshot.checkpoint["cursor"] == "1"
    with pytest.raises(ValueError):
        HandoffSnapshot("id", "scope", "key", _spec(), "f", "unknown", 0)
    with pytest.raises(ValueError):
        HandoffSnapshot("id", "scope", "key", _spec(), "f", "prepared", -1)
    with pytest.raises(ValueError):
        HandoffSnapshot(
            "id", "scope", "key", _spec(), "f", "prepared", 0,
            created_at=datetime(2026, 9, 1, 12),
        )


def test_channel_observation_is_fact_only_and_normalizes_safe_values():
    checkpoint = {"run_id": "run-1"}
    safe_data = {"status": "running"}
    observation = ChannelObservation(
        phase="active",
        checkpoint=checkpoint,
        safe_data=safe_data,
        next_advance_at=datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
    )

    checkpoint["run_id"] = "changed"
    safe_data["status"] = "changed"

    assert observation.checkpoint["run_id"] == "run-1"
    assert observation.safe_data["status"] == "running"
    with pytest.raises(ValueError):
        ChannelObservation(phase="unknown")
    with pytest.raises(ValueError):
        ChannelObservation(phase="active", next_advance_at=datetime(2026, 9, 1, 12))


@pytest.mark.parametrize("field, value", [
    ("binding", {"nested": {"token": "secret"}}),
    ("checkpoint", {"headers": {"Authorization": "Bearer secret"}}),
    ("terminal_result", {"provider_error": "unredacted upstream response"}),
])
def test_snapshot_rejects_unsafe_durable_facts(field: str, value: dict[str, object]):
    values = {field: value}

    with pytest.raises(ValueError):
        HandoffSnapshot(
            "id", "scope", "key", _spec(), "f", "prepared", 0, **values,
        )


@pytest.mark.parametrize("values", [
    {"checkpoint": {"profile_home": "/Users/example/.hermes/profiles/reviewer"}},
    {"binding": {"raw_headers": {"x-request-id": "request-1"}}},
    {"safe_data": {"upstream_error": "raw provider exception"}},
    {"safe_data": {"nested": {"authorization": "Basic secret"}}},
])
def test_observation_rejects_unsafe_durable_facts(values: dict[str, object]):
    with pytest.raises(ValueError):
        ChannelObservation(phase="active", **values)
