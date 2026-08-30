from __future__ import annotations

from copy import deepcopy

import pytest
import yaml

import plugins.workflow.language as workflow_language
from plugins.workflow.language import supports_phase6_semantics
from plugins.workflow.language import (
    make_language_snapshot,
    read_language_snapshot,
    verify_language_snapshot,
    WorkflowLanguageCompatibilityError,
)
from plugins.workflow.models import (
    LoopGroupChildScope,
    WorkflowLanguageProfile,
    WorkflowValidationError,
)
from plugins.workflow.schema import (
    _compile_workflow_source_document,
    load_workflow,
    load_workflow_snapshot,
    parse_workflow_source_bytes,
)
from plugins.workflow.topology import (
    iter_scoped_workflow_nodes,
    primary_terminal_node,
    stable_node_layers,
)


def _load_v6(path):
    document = yaml.safe_load(path.read_bytes())
    pending = list(document.get("nodes", ()))
    commands = path.parent / "commands"
    while pending:
        node = pending.pop()
        group = node.get("loop_group")
        if isinstance(group, dict):
            pending.extend(group.get("nodes", ()))
        command = node.get("command")
        if isinstance(command, str):
            commands.mkdir(exist_ok=True)
            (commands / f"{command}.md").write_text(
                f"fixture command: {command}\n", encoding="utf-8"
            )
    sidecar = path.with_name(f"{path.stem}.hermes.yaml")
    sidecar.write_text("language_compatibility: archon-2026-07\n", encoding="utf-8")
    return load_workflow_snapshot(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=sidecar.read_bytes(),
        normalizer_version=6,
    )


def _normalize_v6_without_admission(path):
    sidecar = b"language_compatibility: archon-2026-07\n"
    source = parse_workflow_source_bytes(
        path,
        workflow_bytes=path.read_bytes(),
        sidecar_bytes=sidecar,
        source="project",
        precedence=1,
    )
    return _compile_workflow_source_document(source, normalizer_version=6)


def _group(body=None, **overrides):
    value = {
        "until": "<promise>DONE</promise>",
        "max_iterations": 25,
        "nodes": body
        if body is not None
        else [
            {"id": "read", "command": "read-item"},
            {
                "id": "record",
                "depends_on": ["read"],
                "command": "record-item",
            },
        ],
    }
    value.update(overrides)
    return {"id": "process-items", "loop_group": value}


def _assert_issue(path, code, expected_path):
    with pytest.raises(WorkflowValidationError) as raised:
        _load_v6(path)
    issue = raised.value.issues[0]
    assert issue.code == code
    assert issue.path == expected_path


def test_v6_normalizes_one_bounded_loop_group(tmp_path, workflow_writer):
    path = workflow_writer(
        tmp_path,
        name="bounded-group",
        filename="bounded-group.yaml",
        nodes=[_group()],
    )

    package = _load_v6(path)
    group = package.definition.nodes[0]

    assert group.node_type == "loop_group"
    assert tuple(node.id for node in group.value["nodes"]) == ("read", "record")
    assert group.value["fresh_context"] is False
    assert primary_terminal_node(group.value["nodes"]).id == "record"
    assert stable_node_layers(group.value["nodes"]) == (
        (group.value["nodes"][0],),
        (group.value["nodes"][1],),
    )
    assert [
        (item.group_id, item.semantic_id, item.authored_path, item.group_options)
        for item in iter_scoped_workflow_nodes(package.definition)
    ] == [
        (None, "process-items", "nodes[0]", None),
        (
            "process-items",
            "process-items/read",
            "nodes[0].loop_group.nodes[0]",
            group.options,
        ),
        (
            "process-items",
            "process-items/record",
            "nodes[0].loop_group.nodes[1]",
            group.options,
        ),
    ]


def test_v6_promotes_primary_sink_output_and_scopes_body_semantics(
    tmp_path, workflow_writer
):
    sink_schema = {
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
        "additionalProperties": False,
    }
    outer_schema = {
        "type": "object",
        "properties": {"preparation": {"type": "string"}},
        "required": ["preparation"],
        "additionalProperties": False,
    }
    path = workflow_writer(
        tmp_path,
        nodes=[
            {
                "id": "prepare",
                "prompt": "prepare",
                "output_format": outer_schema,
            },
            {
                **_group([
                    {
                        "id": "read",
                        "prompt": "Read $prepare.output.preparation",
                    },
                    {
                        "id": "record",
                        "depends_on": ["read"],
                        "prompt": "Record $read.output and $LOOP_PREV.record.output",
                        "output_format": sink_schema,
                        "model": "large",
                    },
                ]),
                "depends_on": ["prepare"],
                "model": "medium",
            },
            {
                "id": "publish",
                "prompt": "Publish $process-items.output.status",
                "depends_on": ["process-items"],
            },
        ],
    )

    package = _load_v6(path)
    group = package.definition.nodes[1]

    assert (
        package.language.structured_outputs["process-items"]
        == (package.language.structured_outputs["process-items/record"])
    )
    assert "process-items/read" in package.language.node_semantics
    assert "process-items/record" in package.language.node_semantics
    assert "model" not in group.value["nodes"][0].options
    assert group.value["nodes"][1].options["model"] == "large"
    assert (
        package.language.node_semantics["process-items/read"]["provider_portability"][
            "model_references"
        ]["primary"]["reference"]
        == "medium"
    )
    assert (
        package.language.node_semantics["process-items/record"]["provider_portability"][
            "model_references"
        ]["primary"]["reference"]
        == "large"
    )

    package_digest = "a" * 64
    snapshot = make_language_snapshot(package, package_digest).to_dict()
    assert read_language_snapshot(snapshot).to_dict() == snapshot

    for contradictory in (False, True):
        altered = deepcopy(snapshot)
        if contradictory:
            altered["structured_outputs"]["process-items"] = altered[
                "structured_outputs"
            ]["prepare"]
        else:
            altered["structured_outputs"].pop("process-items")
        with pytest.raises(WorkflowLanguageCompatibilityError) as raised:
            verify_language_snapshot(
                package,
                package_digest,
                read_language_snapshot(altered),
            )
        assert raised.value.code == "workflow_language_snapshot_mismatch"


def test_current_v6_admits_loop_group_while_recorded_v5_rejects_it(
    tmp_path, workflow_writer
):
    path = workflow_writer(
        tmp_path,
        nodes=[_group(
            [
                {"id": "read", "bash": "printf item"},
                {"id": "record", "depends_on": ["read"], "bash": "printf done"},
            ],
            max_iterations=1,
        )],
    )
    sidecar = path.with_name(f"{path.stem}.hermes.yaml")
    sidecar.write_text("language_compatibility: archon-2026-07\n", encoding="utf-8")

    with pytest.raises(WorkflowValidationError) as raised:
        load_workflow_snapshot(
            path,
            workflow_bytes=path.read_bytes(),
            sidecar_bytes=sidecar.read_bytes(),
            normalizer_version=5,
        )

    current = load_workflow(path)

    assert raised.value.issues[0].code == "loop_group_version_unsupported"
    assert current.language.normalizer_version == 6
    assert current.definition.nodes[0].node_type == "loop_group"


@pytest.mark.parametrize("node_type", ("bash", "script"))
def test_artifact_free_process_mode_is_v6_only(
    tmp_path, workflow_writer, node_type
):
    node = {"id": "reduce", node_type: "printf ok", "artifacts": False}
    if node_type == "script":
        node.update(script="print('ok')", runtime="uv")
    path = workflow_writer(tmp_path, nodes=[node])
    sidecar = path.with_name(f"{path.stem}.hermes.yaml")
    sidecar.write_text("language_compatibility: archon-2026-07\n", encoding="utf-8")

    with pytest.raises(WorkflowValidationError) as raised:
        load_workflow_snapshot(
            path,
            workflow_bytes=path.read_bytes(),
            sidecar_bytes=sidecar.read_bytes(),
            normalizer_version=5,
        )

    assert raised.value.issues[0].code == "artifacts_version_unsupported"
    assert raised.value.issues[0].path == "nodes[0].artifacts"
    assert load_workflow(path).language.normalizer_version == 6


@pytest.mark.parametrize("value", (None, 0, 1, "false", [], {}))
def test_v6_artifact_free_process_mode_requires_boolean(
    tmp_path, workflow_writer, value
):
    path = workflow_writer(
        tmp_path,
        nodes=[{"id": "reduce", "bash": "printf ok", "artifacts": value}],
    )

    with pytest.raises(WorkflowValidationError) as raised:
        _load_v6(path)

    assert raised.value.issues[0].code == "invalid_artifacts"
    assert raised.value.issues[0].path == "nodes[0].artifacts"


def test_v6_artifact_free_process_mode_is_sealed_in_normalized_identity(
    tmp_path, workflow_writer
):
    enabled_path = workflow_writer(
        tmp_path / "enabled",
        name="artifact-mode",
        nodes=[{"id": "reduce", "bash": "printf ok", "artifacts": True}],
    )
    disabled_path = workflow_writer(
        tmp_path / "disabled",
        name="artifact-mode",
        nodes=[{"id": "reduce", "bash": "printf ok", "artifacts": False}],
    )

    enabled = _normalize_v6_without_admission(enabled_path)
    disabled = _normalize_v6_without_admission(disabled_path)

    assert enabled.definition.nodes[0].options["artifacts"] is True
    assert disabled.definition.nodes[0].options["artifacts"] is False
    assert (
        enabled.language.normalized_definition_digest
        != disabled.language.normalized_definition_digest
    )


def test_v6_reuses_effective_interactivity_invariant(tmp_path, workflow_writer):
    path = workflow_writer(
        tmp_path,
        interactive=False,
        nodes=[_group(signal_completes=False)],
    )

    with pytest.raises(WorkflowValidationError) as raised:
        _load_v6(path)

    assert raised.value.issues[0].code == "loop_group_shape_invalid"


@pytest.mark.parametrize(
    ("mutate", "code", "expected_path"),
    [
        pytest.param(
            lambda group: group["loop_group"].update(nodes=[]),
            "loop_group_shape_invalid",
            "nodes[0].loop_group.nodes",
            id="empty-body",
        ),
        pytest.param(
            lambda group: group["loop_group"].pop("until"),
            "loop_group_shape_invalid",
            "nodes[0].loop_group.until",
            id="missing-until",
        ),
        pytest.param(
            lambda group: group["loop_group"].update(until="   "),
            "loop_group_shape_invalid",
            "nodes[0].loop_group.until",
            id="blank-until",
        ),
        pytest.param(
            lambda group: group["loop_group"].update(max_iterations=0),
            "loop_group_shape_invalid",
            "nodes[0].loop_group.max_iterations",
            id="iterations-low",
        ),
        pytest.param(
            lambda group: group["loop_group"].update(max_iterations=101),
            "loop_group_shape_invalid",
            "nodes[0].loop_group.max_iterations",
            id="iterations-high",
        ),
        pytest.param(
            lambda group: group["loop_group"].update(returns="record"),
            "loop_group_shape_invalid",
            "nodes[0].loop_group.returns",
            id="returns-selector",
        ),
        pytest.param(
            lambda group: group["loop_group"]["nodes"].__setitem__(
                0, {"id": "read", "include": "child"}
            ),
            "loop_group_shape_invalid",
            "nodes[0].loop_group.nodes[0].include",
            id="include",
        ),
        pytest.param(
            lambda group: group["loop_group"]["nodes"].__setitem__(
                0,
                _group()["loop_group"]["nodes"][0]
                | {"loop_group": _group()["loop_group"]},
            ),
            "loop_group_shape_invalid",
            "nodes[0].loop_group.nodes[0]",
            id="nested-group",
        ),
        pytest.param(
            lambda group: group["loop_group"]["nodes"].__setitem__(
                0, {"id": "read", "workflow": "child"}
            ),
            "loop_group_shape_invalid",
            "nodes[0].loop_group.nodes[0].workflow",
            id="runtime-workflow",
        ),
        pytest.param(
            lambda group: group.update(retry={"max_attempts": 1}),
            "loop_group_shape_invalid",
            "nodes[0].retry",
            id="group-retry",
        ),
        pytest.param(
            lambda group: group["loop_group"]["nodes"].__setitem__(
                0, {"id": "read", "command": "read", "prompt": "read"}
            ),
            "loop_group_shape_invalid",
            "nodes[0].loop_group.nodes[0]",
            id="node-type-one-of",
        ),
        pytest.param(
            lambda group: group["loop_group"].update(
                nodes=[
                    {"id": "a", "command": "a", "depends_on": ["b"]},
                    {"id": "b", "command": "b", "depends_on": ["a"]},
                ]
            ),
            "loop_group_topology_invalid",
            "nodes[0].loop_group.nodes",
            id="cycle",
        ),
        pytest.param(
            lambda group: group["loop_group"].update(
                nodes=[
                    {"id": "same", "command": "a"},
                    {"id": "same", "command": "b"},
                ]
            ),
            "loop_group_topology_invalid",
            "nodes[0].loop_group.nodes[1].id",
            id="duplicate-body-id",
        ),
        pytest.param(
            lambda group: group["loop_group"]["nodes"][1].update(
                depends_on=["outside"]
            ),
            "loop_group_scope_invalid",
            "nodes[0].loop_group.nodes[1].depends_on",
            id="dependency-outside-body",
        ),
    ],
)
def test_v6_rejects_invalid_group_contract(
    tmp_path, workflow_writer, mutate, code, expected_path
):
    group = deepcopy(_group())
    mutate(group)
    path = workflow_writer(tmp_path, nodes=[group])
    _assert_issue(path, code, expected_path)


def test_v6_rejects_more_than_512_body_nodes(tmp_path, workflow_writer):
    body = [{"id": f"n{index}", "command": "run"} for index in range(513)]
    path = workflow_writer(tmp_path, nodes=[_group(body)])

    _assert_issue(
        path,
        "loop_group_product_limit",
        "nodes[0].loop_group.nodes",
    )


def test_v6_rejects_more_than_4096_body_edges(tmp_path, workflow_writer):
    body = [
        {
            "id": f"n{index}",
            "command": "run",
            **(
                {"depends_on": [f"n{prior}" for prior in range(index)]} if index else {}
            ),
        }
        for index in range(92)
    ]
    path = workflow_writer(tmp_path, nodes=[_group(body)])

    _assert_issue(
        path,
        "loop_group_product_limit",
        "nodes[0].loop_group.nodes",
    )


def test_v6_rejects_worst_case_child_attempt_product(tmp_path, workflow_writer):
    body = [{"id": f"n{index}", "prompt": "run"} for index in range(14)]
    path = workflow_writer(
        tmp_path,
        nodes=[_group(body, max_iterations=100)],
    )

    _assert_issue(
        path,
        "loop_group_product_limit",
        "nodes[0].loop_group",
    )


def test_v6_counts_approval_rework_in_child_attempt_product(tmp_path, workflow_writer):
    body = [
        {
            "id": f"approve{index}",
            "approval": {
                "message": "Continue?",
                "on_reject": {"prompt": "Revise", "max_attempts": 10},
            },
        }
        for index in range(5)
    ]
    path = workflow_writer(tmp_path, nodes=[_group(body, max_iterations=100)])

    _assert_issue(path, "loop_group_product_limit", "nodes[0].loop_group")


def test_v6_accepts_exact_4096_child_attempt_product(tmp_path, workflow_writer):
    body = [
        {
            "id": f"approve{index}",
            "approval": {
                "message": "Continue?",
                "on_reject": {"prompt": "Revise", "max_attempts": 7},
            },
        }
        for index in range(8)
    ]
    path = workflow_writer(tmp_path, nodes=[_group(body, max_iterations=64)])

    assert (
        _normalize_v6_without_admission(path).definition.nodes[0].node_type
        == "loop_group"
    )


def _semantic_bound_group(group_index, child_count):
    return {
        "id": f"group{group_index}",
        "loop_group": {
            "until": "done",
            "max_iterations": 1,
            "nodes": [
                {"id": f"child{index}", "prompt": "run"}
                for index in range(child_count)
            ],
        },
    }


def test_v6_rejects_more_than_1024_scoped_semantic_entries(tmp_path, workflow_writer):
    path = workflow_writer(
        tmp_path,
        nodes=[_semantic_bound_group(index, 341) for index in range(3)],
    )

    with pytest.raises(WorkflowValidationError) as raised:
        _normalize_v6_without_admission(path)
    issue = raised.value.issues[0]
    assert issue.code == "loop_group_product_limit"
    assert issue.path == "nodes[2].loop_group"


def test_v6_snapshot_round_trips_at_1024_scoped_semantic_entries(
    tmp_path, workflow_writer
):
    path = workflow_writer(
        tmp_path,
        nodes=[
            _semantic_bound_group(0, 340),
            _semantic_bound_group(1, 340),
            _semantic_bound_group(2, 341),
        ],
    )

    package = _normalize_v6_without_admission(path)
    snapshot = make_language_snapshot(package, "a" * 64).to_dict()

    assert len(snapshot["node_semantics"]) == 1024
    assert read_language_snapshot(snapshot).to_dict() == snapshot


@pytest.mark.parametrize(
    ("field", "template", "depends_on", "code"),
    [
        (
            "until_bash",
            "test -n '$missing.output'",
            (),
            "output_reference_not_declared_dependency",
        ),
        (
            "gate_message",
            "Review $outer.output",
            (),
            "output_reference_not_declared_dependency",
        ),
        (
            "gate_message",
            "Review $outer.output.missing",
            ("outer",),
            "structured_output_field_impossible",
        ),
    ],
)
def test_v6_validates_group_control_template_references(
    tmp_path, workflow_writer, field, template, depends_on, code
):
    outer_schema = {
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "required": ["status"],
        "additionalProperties": False,
    }
    group = _group(**{field: template})
    group["depends_on"] = list(depends_on)
    path = workflow_writer(
        tmp_path,
        nodes=[
            {"id": "outer", "prompt": "prepare", "output_format": outer_schema},
            group,
        ],
    )

    _assert_issue(path, code, f"nodes[1].loop_group.{field}")


@pytest.mark.parametrize(
    ("prompt", "depends_on", "expected_path"),
    [
        (
            "Use $outer.output",
            (),
            "nodes[1].loop_group.nodes[0].prompt",
        ),
        (
            "Use $missing.output",
            ("outer",),
            "nodes[1].loop_group.nodes[0].prompt",
        ),
        (
            "Use $LOOP_PREV.missing.output",
            ("outer",),
            "nodes[1].loop_group.nodes[0].prompt",
        ),
    ],
)
def test_v6_rejects_invalid_body_reference_scopes(
    tmp_path, workflow_writer, prompt, depends_on, expected_path
):
    group = _group([{"id": "read", "prompt": prompt}])
    group["depends_on"] = list(depends_on)
    path = workflow_writer(
        tmp_path,
        nodes=[{"id": "outer", "command": "outer"}, group],
    )

    _assert_issue(path, "loop_group_scope_invalid", expected_path)


def test_loop_group_child_scope_is_validated_and_deterministic():
    scope = LoopGroupChildScope(
        run_id="a" * 32,
        group_id="group",
        controller_generation=2,
        iteration=3,
        node_id="body",
    )

    assert scope.worker_node_id == f"loop-group/{'a' * 32}/group/2/0003/body"
    for changes in (
        {"run_id": "../run"},
        {"group_id": "bad/group"},
        {"node_id": "bad/node"},
        {"controller_generation": 0},
        {"iteration": 0},
    ):
        values = {
            "run_id": "a" * 32,
            "group_id": "group",
            "controller_generation": 2,
            "iteration": 3,
            "node_id": "body",
            **changes,
        }
        with pytest.raises(ValueError):
            LoopGroupChildScope(**values)


def test_phase6_reader_is_supported_and_current_for_archon():
    profile = WorkflowLanguageProfile.ARCHON_2026_07

    assert workflow_language.SUPPORTED_NORMALIZER_VERSIONS == {1, 2, 3, 4, 5, 6}
    assert workflow_language.LATEST_NORMALIZER_VERSION == 6
    assert workflow_language.CURRENT_NORMALIZER_BY_PROFILE[profile] == 6
    assert supports_phase6_semantics(profile, 5) is False
    assert supports_phase6_semantics(profile, 6) is True
