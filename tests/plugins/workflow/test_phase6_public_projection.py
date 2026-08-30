from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from plugins.workflow.sanitize import public_event_projection, public_run_projection


_CANARY = "PRIVATE_LOOP_GROUP_CANARY_20260829"


def _api_module():
    path = Path(__file__).parents[3] / "plugins/workflow/dashboard/plugin_api.py"
    name = "workflow_phase6_public_projection_api"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _run(loop_group: object) -> dict[str, object]:
    return {
        "run_id": "run-1",
        "workflow": "bounded-group",
        "status": "running",
        "status_authoritative": True,
        "health": "healthy",
        "updated_at": "2026-08-29T12:00:00+00:00",
        "state_version": 7,
        "attempts": 0,
        "next_actions": ["cancel"],
        "current_nodes": ["group"],
        "progress": {"kind": "graph", "completed_nodes": 0, "total_nodes": 1},
        "nodes": {
            "group": {
                "id": "group",
                "type": "loop_group",
                "state": "running",
                "depends_on": [],
                "attempts": [],
                "loop_group": loop_group,
            }
        },
        "artifacts": [],
    }


def _controller() -> dict[str, object]:
    body = {}
    for index in range(513):
        body[f"body-{index:03d}"] = {
            "id": f"body-{index:03d}",
            "type": "bash",
            "state": "succeeded" if index < 3 else "pending",
            "attempts": [{"attempt_id": f"attempt-{index}", "prompt": _CANARY}],
            "started_at": "2026-08-29T12:00:00+00:00",
            "completed_at": "2026-08-29T12:00:00.125000+00:00",
            "error_code": "categorical_failure" if index == 2 else None,
            "prompt": _CANARY,
            "command": _CANARY,
            "tool_result": _CANARY,
            "feedback": _CANARY,
            "output": _CANARY,
            "environment": {"TOKEN": _CANARY},
            "path": f"/private/{_CANARY}",
        }
    return {
        "schema_version": 1,
        "controller_generation": 2,
        "iteration": 30,
        "max_iterations": 100,
        "state": "running",
        "primary_sink": "body-002",
        "body": body,
        "iterations": [
            {
                "iteration": iteration,
                "state": "succeeded",
                "completed_nodes": 3,
                "total_nodes": 3,
                "started_at": "2026-08-29T12:00:00+00:00",
                "completed_at": "2026-08-29T12:00:00.250000+00:00",
                "prompt": _CANARY,
                "previous_outputs": {_CANARY: _CANARY},
            }
            for iteration in range(1, 31)
        ],
        "previous_outputs": {_CANARY: _CANARY},
        "pending_interaction": {"feedback": _CANARY},
        "credentials": _CANARY,
    }


def test_loop_group_projection_is_closed_bounded_and_definition_ordered() -> None:
    public = public_run_projection(_run(_controller()))
    group = public["nodes"]["group"]["loop_group"]

    assert group["iteration"] == 30
    assert group["max_iterations"] == 100
    assert group["completed_iterations"] == 29
    assert group["primary_sink"] == "body-002"
    assert len(group["body"]) == 512
    assert [item["id"] for item in group["body"][:3]] == [
        "body-000",
        "body-001",
        "body-002",
    ]
    assert group["body"][0]["duration_ms"] == 125
    assert group["body"][2]["failure_code"] == "categorical_failure"
    assert [item["iteration"] for item in group["iterations"]] == list(range(6, 31))
    assert group["iterations"][0]["duration_ms"] == 250
    assert _CANARY not in json.dumps(public)
    _api_module().WorkflowRunProjection.model_validate(public)


@pytest.mark.parametrize(
    ("state", "iteration", "max_iterations", "body_states", "expected"),
    [
        ("running", 7, 25, ("succeeded", "running"), 6),
        ("paused", 7, 25, ("succeeded", "skipped"), 7),
        ("succeeded", 7, 25, ("succeeded", "skipped"), 7),
        ("failed", 25, 25, ("succeeded", "skipped"), 25),
    ],
)
def test_runtime_loop_group_completed_iterations_come_from_current_body_state(
    state: str,
    iteration: int,
    max_iterations: int,
    body_states: tuple[str, ...],
    expected: int,
) -> None:
    controller = {
        "schema_version": 1,
        "controller_generation": 2,
        "iteration": iteration,
        "max_iterations": max_iterations,
        "state": state,
        "primary_sink": "sink",
        "body": {
            node_id: {
                "id": node_id,
                "type": "bash",
                "state": body_state,
                "attempts": [],
            }
            for node_id, body_state in zip(
                ("prepare", "sink"), body_states, strict=True
            )
        },
    }

    public = public_run_projection(_run(controller))

    assert public["nodes"]["group"]["loop_group"]["completed_iterations"] == expected


def test_loop_group_duration_rejects_sub_millisecond_timestamp_reversal() -> None:
    controller = _controller()
    controller["body"]["body-000"].update(
        started_at="2026-08-29T12:00:00.000500+00:00",
        completed_at="2026-08-29T12:00:00+00:00",
    )

    public = public_run_projection(_run(controller))

    assert "duration_ms" not in public["nodes"]["group"]["loop_group"]["body"][0]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(iteration=True),
        lambda value: value.update(max_iterations=101),
        lambda value: value.update(primary_sink="x" * 129),
        lambda value: value.update(body=[]),
        lambda value: value.update(iterations=[{"iteration": 1}]),
    ],
)
def test_malformed_loop_group_is_omitted_without_weakening_run(mutation) -> None:
    controller = _controller()
    mutation(controller)

    public = public_run_projection(_run(controller))

    assert "loop_group" not in public["nodes"]["group"]
    _api_module().WorkflowRunProjection.model_validate(public)


def test_loop_group_models_reject_nested_extras_and_bounds() -> None:
    module = _api_module()
    public = public_run_projection(_run(_controller()))
    public["nodes"]["group"]["loop_group"]["body"][0]["private_extra"] = _CANARY

    with pytest.raises(ValidationError):
        module.WorkflowRunProjection.model_validate(public)


@pytest.mark.parametrize(
    "event_type",
    ["interaction_approved", "interaction_rejected", "node_reconciled"],
)
def test_nested_generic_event_scope_is_exact_and_top_level_events_omit_it(
    event_type: str,
) -> None:
    scoped = public_event_projection(
        {
            "sequence": 7,
            "timestamp": "2026-08-29T12:00:00+00:00",
            "run_id": "run-1",
            "event_type": event_type,
            "payload": {
                "loop_group_scope": {
                    "group_id": "group",
                    "controller_generation": 2,
                    "iteration": 7,
                    "node_id": "sink",
                    "output": _CANARY,
                },
                "decision": "continue",
                "output": _CANARY,
            },
        }
    )
    top_level = public_event_projection(
        {
            "sequence": 8,
            "timestamp": "2026-08-29T12:00:01+00:00",
            "run_id": "run-1",
            "event_type": event_type,
            "payload": {"loop_group_scope": None},
        }
    )

    assert scoped["loop_group_scope"] == {
        "group_id": "group",
        "controller_generation": 2,
        "iteration": 7,
        "body_node_id": "sink",
    }
    assert "loop_group_scope" not in top_level
    assert _CANARY not in json.dumps(scoped)
    _api_module().WorkflowTimelineEventProjection.model_validate(scoped)


def test_interaction_evidence_scope_model_is_closed() -> None:
    module = _api_module()
    interaction = {
        "item_type": "interaction",
        "sequence": 9,
        "event_type": "interaction_approved",
        "loop_group_scope": {
            "group_id": "group",
            "controller_generation": 2,
            "iteration": 7,
            "body_node_id": "sink",
        },
    }

    module.WorkflowInteractionEvidenceProjection.model_validate(interaction)

    interaction["loop_group_scope"]["output"] = _CANARY
    with pytest.raises(ValidationError):
        module.WorkflowInteractionEvidenceProjection.model_validate(interaction)
