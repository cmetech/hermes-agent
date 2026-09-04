from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import cast

import pytest
import yaml

import plugins.workflow.language as workflow_language
import plugins.workflow.language_schema as language_schema
from plugins.workflow.language import supports_phase6_semantics
from plugins.workflow.language import (
    make_language_snapshot,
    read_language_snapshot,
    verify_language_snapshot,
    WorkflowLanguageCompatibilityError,
)
from plugins.workflow.language_schema import workflow_authoring_contract
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


_MISSING = object()


def _contract_path_value(value, path):
    current = value
    for field in path:
        if not isinstance(current, Mapping) or field not in current:
            return _MISSING
        current = current[field]
    return current


def _evaluate_contract_selector(name, formula, descriptor, node):
    node_kind = next(kind for kind in language_schema.NODE_TYPES if kind in node)
    if name == "ordinary_loop_multiplier":
        if node_kind != "loop":
            return descriptor["ordinary_loop_default_multiplier"]
        selected = _contract_path_value(
            node, descriptor["ordinary_loop_multiplier_path"]
        )
        return (
            descriptor["ordinary_loop_default_multiplier"]
            if selected is _MISSING
            else selected
        )

    assert name == "selected_retries"
    for branch in formula["retry_precedence"].split(">"):
        if branch == "approval" and isinstance(
            _contract_path_value(node, ["approval", "on_reject"]), Mapping
        ):
            selected = _contract_path_value(
                node, descriptor["approval_max_attempts_path"]
            )
            return (
                descriptor["approval_default_max_attempts"]
                if selected is _MISSING
                else selected
            )
        if branch == "retry" and isinstance(node.get("retry"), Mapping):
            selected = _contract_path_value(
                node, descriptor["retry_max_attempts_path"]
            )
            return (
                descriptor["other_default_retries"]
                if selected is _MISSING
                else selected
            )
        if branch == "command|prompt" and node_kind in {"command", "prompt"}:
            return descriptor["command_prompt_default_retries"]
        if branch == "default":
            return descriptor["other_default_retries"]
    raise AssertionError("published retry precedence has no default")


def _evaluate_contract_expression(
    expression, formula, descriptor, body, group_iterations, node=None
):
    if isinstance(expression, int):
        return expression
    if isinstance(expression, str):
        if expression == "group_iterations":
            return group_iterations
        if expression in {"ordinary_loop_multiplier", "selected_retries"}:
            assert node is not None
            return _evaluate_contract_selector(
                expression, formula, descriptor, node
            )
        raise AssertionError(f"unknown contract reference: {expression}")

    operator, *operands = expression
    if operator == "sum":
        collection, term = operands
        assert collection == "body_nodes"
        return sum(
            _evaluate_contract_expression(
                term,
                formula,
                descriptor,
                body,
                group_iterations,
                node=child,
            )
            for child in body
        )
    values = [
        _evaluate_contract_expression(
            operand,
            formula,
            descriptor,
            body,
            group_iterations,
            node=node,
        )
        for operand in operands
    ]
    if operator == "*":
        return values[0] * values[1]
    if operator == "+":
        return values[0] + values[1]
    raise AssertionError(f"unknown contract operator: {operator}")


def _structured_path_constraint():
    contract = workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07)
    semantic_rules = cast(list[dict[str, object]], contract["semantic_rules"])
    rule = next(
        item
        for item in semantic_rules
        if item["id"] == "scoped-output-reference-v1"
    )
    reference = cast(dict[str, str], rule["semantic_ref"])
    node_kinds = cast(list[dict[str, object]], contract["node_kinds"])
    kind = next(
        item
        for item in node_kinds
        if item["id"] == reference["node_kind"]
    )
    definitions = cast(
        dict[str, dict[str, object]], kind["semantic_definitions"]
    )
    return cast(
        dict[str, object],
        definitions[reference["definition"]]["structured_path_constraint_v1"],
    )


def _resolve_published_producer_schema(constraint, nodes, producer_id):
    producer = next(node for node in nodes if node["id"] == producer_id)
    resolution = constraint["producer_schema_resolution_v1"]
    if "loop_group" not in producer:
        return _contract_path_value(producer, resolution["ordinary"])

    group_policy = resolution["loop_group"]
    assert group_policy[0] == "scoped-dag-topology-v1.primary_sink"
    body = producer["loop_group"]["nodes"]
    depended_on = {
        dependency for node in body for dependency in node.get("depends_on", ())
    }
    sink = next(node for node in body if node["id"] not in depended_on)
    return _contract_path_value(sink, group_policy[1:])


def _published_path_outcome(policy, schema, path_parts):
    keywords = policy["strategies"]
    assert policy["accept"] == ["possible", "unknown"]
    assert policy["reject"] == "impossible"

    def local_ref(root, reference):
        if (
            not isinstance(reference, str)
            or not reference.startswith("#/")
        ):
            return _MISSING
        current = root
        for raw_part in reference[2:].split("/"):
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if isinstance(current, Mapping) and part in current:
                current = current[part]
            elif (
                isinstance(current, list | tuple)
                and part.isascii()
                and part.isdigit()
                and int(part) < len(current)
            ):
                current = current[int(part)]
            else:
                return _MISSING
        return current

    def visit(current, remaining, root, resolving):
        if current is False:
            return "impossible"
        if current is True or not isinstance(current, Mapping):
            return "unknown"

        segment = remaining[0]
        index = int(segment) if segment.isascii() and segment.isdigit() else None
        mode_key = "ascii-decimal" if index is not None else "other"
        modes = (
            ("object", "array")
            if policy["modes"][mode_key] == "all(object,array)"
            else (policy["modes"][mode_key],)
        )
        mode_outcomes = [
            interpret(current, remaining, root, resolving, mode, index)
            for mode in modes
        ]
        if all(outcome == "impossible" for outcome in mode_outcomes):
            return "impossible"
        if any(outcome == "possible" for outcome in mode_outcomes):
            return "possible"
        return "unknown"

    def interpret(current, remaining, root, resolving, mode, index):
        schema_type = current.get("type")
        if keywords["type"] == "exclude-mode" and (
            isinstance(schema_type, str) and schema_type != mode
            or isinstance(schema_type, list | tuple) and mode not in schema_type
        ):
            return "impossible"

        reference = current.get("$ref")
        if (
            policy["$ref"].startswith("local-pointer(map/array,~0/~1);")
            and isinstance(reference, str)
            and reference not in resolving
        ):
            target = local_ref(root, reference)
            if target is False:
                return "impossible"
            if isinstance(target, Mapping) and interpret(
                target,
                remaining,
                root,
                resolving | {reference},
                mode,
                index,
            ) == "impossible":
                return "impossible"

        if mode == "object":
            property_name, *tail = remaining
            properties = current.get("properties")
            if isinstance(properties, Mapping) and property_name in properties:
                child = properties[property_name]
                if not tail:
                    local_outcome = "impossible" if child is False else "possible"
                else:
                    local_outcome = visit(child, tuple(tail), root, resolving)
            elif isinstance(current.get("patternProperties"), Mapping) and current[
                "patternProperties"
            ]:
                local_outcome = "unknown"
            else:
                additional = current.get("additionalProperties", True)
                if additional is False:
                    local_outcome = "impossible"
                elif isinstance(additional, Mapping) and tail:
                    local_outcome = visit(additional, tuple(tail), root, resolving)
                else:
                    local_outcome = "unknown"
        elif index is not None:
            maximum = current.get("maxItems")
            if (
                isinstance(maximum, int)
                and not isinstance(maximum, bool)
                and index >= maximum
            ):
                return "impossible"
            prefix = current.get("prefixItems")
            if isinstance(prefix, list | tuple) and index < len(prefix):
                child = prefix[index]
            else:
                items = current.get("items", True)
                if isinstance(items, list | tuple):
                    child = (
                        items[index]
                        if index < len(items)
                        else current.get("additionalItems", True)
                    )
                else:
                    child = items
            local_outcome = (
                ("impossible" if child is False else "possible")
                if len(remaining) == 1
                else visit(child, remaining[1:], root, resolving)
            )
        else:
            return "impossible"

        all_of = current.get("allOf")
        if isinstance(all_of, list | tuple) and any(
            branch is False
            or isinstance(branch, Mapping)
            and interpret(branch, remaining, root, resolving, mode, index)
            == "impossible"
            for branch in all_of
        ):
            return "impossible"
        *union_keywords, union_strategy = keywords["union"]
        assert union_strategy == "nonempty-all-impossible"
        for keyword in union_keywords:
            branches = current.get(keyword)
            if (
                isinstance(branches, list | tuple)
                and branches
                and all(
                    branch is False
                    or isinstance(branch, Mapping)
                    and interpret(branch, remaining, root, resolving, mode, index)
                    == "impossible"
                    for branch in branches
                )
            ):
                return "impossible"
        return local_outcome

    assert policy["strategies"]["unlisted"] == "ignored=unknown"
    assert policy["$ref"].startswith("local-pointer(map/array,~0/~1);")
    return visit(schema, tuple(path_parts), schema, frozenset())


def _published_root_dotted_key(policy, schema, path_parts):
    dotted = policy["dotted_key"]
    assert dotted.startswith("after=impossible;joined-tail=literal-key;")
    schema_type = schema.get("type")
    object_capable = not (
        isinstance(schema_type, str) and schema_type != "object"
    ) and not (
        isinstance(schema_type, list | tuple) and "object" not in schema_type
    )
    properties = schema.get("properties")
    joined = ".".join(path_parts)
    return (
        object_capable
        and "." in joined
        and isinstance(properties, Mapping)
        and joined in properties
    )


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

    package = _normalize_v6_without_admission(path)
    snapshot = make_language_snapshot(package, "a" * 64).to_dict()
    node_semantics = cast(dict[str, dict[str, object]], snapshot["node_semantics"])
    process_semantics = cast(
        dict[str, object], node_semantics["process-items"]["loop_group"]
    )

    assert package.definition.nodes[0].node_type == "loop_group"
    assert process_semantics["child_attempts"] == 4096
    round_trip = read_language_snapshot(snapshot)
    assert round_trip is not None
    assert round_trip.to_dict() == snapshot
    assert workflow_language.LOOP_GROUP_WORK_LIMIT == (
        language_schema.LOOP_GROUP_WORK_LIMIT
    )

    for field in ("child_executions", "child_attempts"):
        over_limit = deepcopy(snapshot)
        node_semantics = cast(dict[str, dict[str, object]], over_limit["node_semantics"])
        process_semantics = cast(
            dict[str, object], node_semantics["process-items"]["loop_group"]
        )
        process_semantics[field] = 4097
        with pytest.raises(
            WorkflowLanguageCompatibilityError,
            match="workflow language snapshot node semantics are invalid",
        ):
            read_language_snapshot(over_limit)


def test_v6_work_product_descriptor_matches_normalized_admission_arithmetic(
    tmp_path, workflow_writer
):
    body = [
        {"id": "command", "command": "run"},
        {
            "id": "prompt",
            "prompt": "run",
            "retry": {"max_attempts": 4},
        },
        {"id": "bash", "bash": "true", "retry": {"max_attempts": 3}},
        {
            "id": "loop",
            "loop": {
                "command": "repeat",
                "until": "done",
                "max_iterations": 4,
            },
        },
        {
            "id": "approval",
            "approval": {
                "message": "Continue?",
                "on_reject": {"prompt": "Revise"},
            },
        },
        {
            "id": "approval-explicit",
            "approval": {
                "message": "Continue?",
                "on_reject": {"prompt": "Revise", "max_attempts": 5},
            },
        },
        {"id": "cancel", "cancel": "stop"},
    ]
    path = workflow_writer(
        tmp_path,
        nodes=[_group(body, max_iterations=2)],
    )

    package = _normalize_v6_without_admission(path)
    semantics = package.language.node_semantics["process-items"]["loop_group"]
    contract = workflow_authoring_contract(
        WorkflowLanguageProfile.ARCHON_2026_07,
        normalizer_version=6,
    )
    semantic_rules = cast(list[dict[str, object]], contract["semantic_rules"])
    work = next(
        rule
        for rule in semantic_rules
        if rule.get("kind") == "loop-group-work-product-v1"
    )
    topology = next(
        rule
        for rule in semantic_rules
        if rule.get("kind") == "scoped-dag-topology-v1"
    )
    reference = cast(dict[str, str], work["semantic_ref"])
    node_kinds = cast(list[dict[str, object]], contract["node_kinds"])
    loop_group_kind = next(
        item
        for item in node_kinds
        if item["id"] == reference["node_kind"]
    )
    semantic_definitions = cast(
        dict[str, dict[str, object]], loop_group_kind["semantic_definitions"]
    )
    formula = semantic_definitions[reference["definition"]]
    expressions = cast(dict[str, object], formula["expressions"])
    accumulators = cast(list[str], work["accumulators"])

    assert formula["expression_format"] == "prefix-v1"
    assert accumulators == ["executions", "attempts"]
    assert set(expressions) == set(accumulators)
    assert work["limit"] == language_schema.LOOP_GROUP_WORK_LIMIT
    assert topology["max_edges"] == language_schema.LOOP_GROUP_MAX_EDGES
    assert [
        _evaluate_contract_selector(
            "ordinary_loop_multiplier", formula, work, node
        )
        for node in body
    ] == [1, 1, 1, 4, 1, 1, 1]
    assert [
        _evaluate_contract_selector("selected_retries", formula, work, node)
        for node in body
    ] == [2, 4, 3, 0, 3, 5, 0]
    evaluated = {
        name: _evaluate_contract_expression(
            expression, formula, work, body, 2
        )
        for name, expression in expressions.items()
    }
    assert evaluated == {"executions": 20, "attempts": 54}
    assert evaluated == {
        "executions": semantics["child_executions"],
        "attempts": semantics["child_attempts"],
    }


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


def test_v6_group_gate_missing_dependency_has_branch_local_semantic_code(
    tmp_path, workflow_writer
):
    group = _group(gate_message="Review $outer.output")
    path = workflow_writer(
        tmp_path,
        nodes=[{"id": "outer", "prompt": "prepare"}, group],
    )

    with pytest.raises(WorkflowValidationError) as raised:
        _normalize_v6_without_admission(path)

    issue = raised.value.issues[0]
    assert (
        issue.code,
        issue.path,
        getattr(issue, "semantic_code", None),
    ) == (
        "output_reference_not_declared_dependency",
        "nodes[1].loop_group.gate_message",
        "scoped-reference-missing-dependency",
    )


@pytest.mark.parametrize(
    (
        "scope",
        "surface",
        "producer_schema",
        "path_part",
        "native_code",
        "semantic_code",
        "condition",
    ),
    [
        (
            "current",
            "body",
            None,
            "status",
            "loop_group_scope_invalid",
            "scoped-reference-producer-schema-required",
            "no_schema",
        ),
        (
            "outer",
            "body",
            {"type": "object", "properties": {}, "additionalProperties": False},
            "missing",
            "loop_group_scope_invalid",
            "scoped-reference-structured-path-impossible",
            "impossible",
        ),
        (
            "previous_iteration",
            "body",
            None,
            "status",
            "loop_group_scope_invalid",
            "scoped-reference-producer-schema-required",
            "no_schema",
        ),
        (
            "current",
            "until_bash",
            {"type": "object", "properties": {}, "additionalProperties": False},
            "missing",
            "loop_group_scope_invalid",
            "scoped-reference-structured-path-impossible",
            "impossible",
        ),
        (
            "outer",
            "gate_message",
            None,
            "status",
            "output_reference_path_unsupported",
            "scoped-reference-producer-schema-required",
            "no_schema",
        ),
        (
            "outer",
            "gate_message",
            {
                "type": "object",
                "properties": {"record.status": {"type": "string"}},
                "additionalProperties": False,
            },
            "record.status",
            "output_reference_path_unsupported",
            "scoped-reference-structured-path-impossible",
            "dotted",
        ),
    ],
)
def test_v6_scoped_structured_reference_diagnostics_are_branch_local(
    tmp_path,
    workflow_writer,
    scope,
    surface,
    producer_schema,
    path_part,
    native_code,
    semantic_code,
    condition,
):
    producer = {"id": "producer", "prompt": "produce"}
    if producer_schema is not None:
        producer["output_format"] = producer_schema
    outer_nodes = []
    body = []
    group_dependencies = []
    group_options = {}
    if scope == "outer":
        outer_nodes.append(producer)
        group_dependencies.append("producer")
        reference = f"$producer.output.{path_part}"
        body.append({
            "id": "consumer",
            "prompt": reference if surface == "body" else "consume",
        })
        body_index = 0
    else:
        body.append(producer)
        prefix = "$LOOP_PREV." if scope == "previous_iteration" else "$"
        reference = f"{prefix}producer.output.{path_part}"
        body.append({
            "id": "consumer",
            "depends_on": ["producer"],
            "prompt": reference if surface == "body" else "consume",
        })
        body_index = 1
    if surface == "until_bash":
        group_options["until_bash"] = f"test -n '{reference}'"
    elif surface == "gate_message":
        group_options["gate_message"] = reference
    group = _group(body, **group_options)
    group["depends_on"] = group_dependencies
    path = workflow_writer(tmp_path, nodes=[*outer_nodes, group])

    with pytest.raises(WorkflowValidationError) as raised:
        _normalize_v6_without_admission(path)

    issue = raised.value.issues[0]
    group_index = 1 if outer_nodes else 0
    expected_path = (
        f"nodes[{group_index}].loop_group.nodes[{body_index}].prompt"
        if surface == "body"
        else f"nodes[{group_index}].loop_group.{surface}"
    )
    assert (issue.code, issue.path, getattr(issue, "semantic_code", None)) == (
        native_code,
        expected_path,
        semantic_code,
    )
    contract = workflow_authoring_contract(WorkflowLanguageProfile.ARCHON_2026_07)
    semantic_rules = cast(list[dict[str, object]], contract["semantic_rules"])
    reference_rule = next(
        rule
        for rule in semantic_rules
        if rule["id"] == "scoped-output-reference-v1"
    )
    semantic_ref = cast(dict[str, str], reference_rule["semantic_ref"])
    node_kinds = cast(list[dict[str, object]], contract["node_kinds"])
    loop_group_kind = next(
        kind
        for kind in node_kinds
        if kind["id"] == semantic_ref["node_kind"]
    )
    semantic_definitions = cast(
        dict[str, dict[str, object]], loop_group_kind["semantic_definitions"]
    )
    reference_definition = semantic_definitions[semantic_ref["definition"]]
    constraint = cast(
        dict[str, object], reference_definition["structured_path_constraint_v1"]
    )
    table = cast(dict[str, list[object]], constraint["diagnostic_table"])
    columns = cast(list[str], table["cols"])
    rows = cast(list[list[str]], table["rows"])
    diagnostic = next(
        dict(zip(columns, row, strict=True))
        for row in rows
        if row[0] == condition
    )
    assert diagnostic["semantic"] == getattr(issue, "semantic_code")
    descriptor_surface = {
        "body": "body",
        "until_bash": "until",
        "gate_message": "gate",
    }[surface]
    assert table["codes"][diagnostic[descriptor_surface]] == issue.code


def test_v6_accepts_structured_paths_on_all_scoped_reference_surfaces(
    tmp_path, workflow_writer
):
    schema = {
        "type": "object",
        "properties": {"status": {"type": "string"}},
        "additionalProperties": False,
    }
    path = workflow_writer(
        tmp_path,
        nodes=[
            {"id": "outer", "prompt": "prepare", "output_format": schema},
            {
                **_group(
                    [
                        {
                            "id": "producer",
                            "prompt": "produce",
                            "output_format": schema,
                        },
                        {
                            "id": "consumer",
                            "depends_on": ["producer"],
                            "prompt": (
                                "$producer.output.status "
                                "$outer.output.status "
                                "$LOOP_PREV.producer.output.status"
                            ),
                        },
                    ],
                    until_bash=(
                        'test "$producer.output.status $outer.output.status '
                        '$LOOP_PREV.producer.output.status" = expected'
                    ),
                    gate_message="Review $outer.output.status",
                ),
                "depends_on": ["outer"],
            },
        ],
    )

    assert _normalize_v6_without_admission(path).definition.nodes[1].node_type == (
        "loop_group"
    )


@pytest.mark.parametrize(
    ("surface", "field", "schema", "expected_outcome"),
    [
        (
            "body",
            "status",
            {
                "type": "object",
                "properties": {"status": {"type": "string"}},
                "additionalProperties": False,
            },
            "possible",
        ),
        (
            "gate_message",
            "status",
            {
                "type": "object",
                "properties": {"status": {"type": "string"}},
                "additionalProperties": False,
            },
            "possible",
        ),
        (
            "body",
            "missing",
            {
                "type": "object",
                "properties": {"status": {"type": "string"}},
                "additionalProperties": False,
            },
            "impossible",
        ),
        (
            "gate_message",
            "missing",
            {
                "type": "object",
                "properties": {"status": {"type": "string"}},
                "additionalProperties": False,
            },
            "impossible",
        ),
        (
            "gate_message",
            "record.status",
            {
                "type": "object",
                "properties": {"record.status": {"type": "string"}},
                "additionalProperties": False,
            },
            "impossible",
        ),
        (
            "body",
            "forbidden",
            {"type": "object", "not": {"required": ["forbidden"]}},
            "unknown",
        ),
        (
            "body",
            "records.1",
            {
                "type": "object",
                "properties": {
                    "records": {"type": "array", "maxItems": 1, "items": True}
                },
                "additionalProperties": False,
            },
            "impossible",
        ),
        (
            "body",
            "record.missing",
            {
                "$defs": {
                    "record": {
                        "type": "object",
                        "properties": {"status": {"type": "string"}},
                        "additionalProperties": False,
                    }
                },
                "type": "object",
                "properties": {"record": {"$ref": "#/$defs/record"}},
                "additionalProperties": False,
            },
            "impossible",
        ),
        (
            "body",
            "choice.missing",
            {
                "type": "object",
                "properties": {
                    "choice": {
                        "anyOf": [
                            {"type": "object", "additionalProperties": False},
                            {"type": "object", "additionalProperties": False},
                        ]
                    }
                },
                "additionalProperties": False,
            },
            "impossible",
        ),
        (
            "body",
            "dynamic",
            {
                "type": "object",
                "patternProperties": {"^dynamic$": {"type": "string"}},
                "additionalProperties": False,
            },
            "unknown",
        ),
    ],
)
def test_v6_group_producer_schema_and_path_decisions_follow_published_policy(
    tmp_path,
    workflow_writer,
    surface,
    field,
    schema,
    expected_outcome,
):
    producer_group = {
        "id": "producer-group",
        "loop_group": {
            "until": "done",
            "max_iterations": 1,
            "nodes": [
                {
                    "id": "first-terminal",
                    "prompt": "produce",
                    "output_format": schema,
                },
                {
                    "id": "second-terminal",
                    "prompt": "do not promote",
                    "output_format": {
                        "type": "object",
                        "properties": {"other": {"type": "string"}},
                        "additionalProperties": False,
                    },
                },
            ],
        },
    }
    reference = f"$producer-group.output.{field}"
    consumer_group = _group(
        [{"id": "consume", "prompt": reference if surface == "body" else "use"}],
        **({"gate_message": reference} if surface == "gate_message" else {}),
    )
    consumer_group["depends_on"] = ["producer-group"]
    nodes = [producer_group, consumer_group]
    constraint = _structured_path_constraint()
    resolved = _resolve_published_producer_schema(
        constraint, nodes, "producer-group"
    )
    assert resolved is not _MISSING
    outcome = _published_path_outcome(
        constraint["conservative_tristate_v1"], resolved, field.split(".")
    )
    assert outcome == expected_outcome
    path = workflow_writer(tmp_path, nodes=nodes)

    if outcome in constraint["conservative_tristate_v1"]["accept"]:
        package = _normalize_v6_without_admission(path)
        assert package.language.structured_outputs["producer-group"] == (
            package.language.structured_outputs["producer-group/first-terminal"]
        )
        return

    with pytest.raises(WorkflowValidationError) as raised:
        _normalize_v6_without_admission(path)
    issue = raised.value.issues[0]
    table = constraint["diagnostic_table"]
    condition = (
        "dotted"
        if _published_root_dotted_key(
            constraint["conservative_tristate_v1"], resolved, field.split(".")
        )
        else "impossible"
    )
    diagnostic = next(
        dict(zip(table["cols"], row, strict=True))
        for row in table["rows"]
        if row[0] == condition
    )
    contract_surface = "body" if surface == "body" else "gate"
    assert (issue.code, getattr(issue, "semantic_code", None)) == (
        table["codes"][diagnostic[contract_surface]],
        diagnostic["semantic"],
    )


def test_v6_admits_all_group_until_bash_reference_scopes(
    tmp_path, workflow_writer
) -> None:
    path = workflow_writer(
        tmp_path,
        nodes=[
            {"id": "outer", "prompt": "prepare"},
            {
                **_group(
                    [{"id": "sink", "prompt": "produce"}],
                    until_bash=(
                        "test \"$sink.output|$outer.output|"
                        "$LOOP_PREV.sink.output\" = expected"
                    ),
                ),
                "depends_on": ["outer"],
            },
        ],
    )

    package = _load_v6(path)

    assert package.definition.nodes[1].value["until_bash"].endswith("= expected")


@pytest.mark.parametrize(
    ("template", "expected_code"),
    [
        ("test -n '$LOOP_PREV.missing.output'", "loop_group_scope_invalid"),
        ("test -n '$sink.output.missing'", "loop_group_scope_invalid"),
    ],
)
def test_v6_rejects_invalid_group_until_bash_scoped_references(
    tmp_path, workflow_writer, template, expected_code
) -> None:
    path = workflow_writer(
        tmp_path,
        nodes=[
            _group(
                [{"id": "sink", "prompt": "produce"}],
                until_bash=template,
            )
        ],
    )

    _assert_issue(
        path,
        expected_code,
        "nodes[0].loop_group.until_bash",
    )


@pytest.mark.parametrize(
    ("surface", "template", "expected_path"),
    [
        (
            "prompt",
            "$LOOP_PREV.producer.outputx",
            "nodes[0].loop_group.nodes[1].prompt",
        ),
        (
            "script",
            "print('$LOOP_PREV.producer.output_')",
            "nodes[0].loop_group.nodes[1].script",
        ),
        (
            "until_bash",
            "printf '%s' \"$LOOP_PREV.producer.output/path\"",
            "nodes[0].loop_group.until_bash",
        ),
        (
            "until_bash",
            "printf '%s' \"$LOOP_PREV.producer.output.missing\"",
            "nodes[0].loop_group.until_bash",
        ),
    ],
)
def test_v6_rejects_malformed_previous_reference_boundaries(
    tmp_path, workflow_writer, surface, template, expected_path
) -> None:
    body = [{"id": "producer", "prompt": "produce"}]
    group_options = {}
    if surface == "until_bash":
        group_options["until_bash"] = template
    else:
        body.append({
            "id": "consumer",
            "depends_on": ["producer"],
            surface: template,
            **({"runtime": "uv"} if surface == "script" else {}),
        })
    path = workflow_writer(tmp_path, nodes=[_group(body, **group_options)])

    _assert_issue(path, "loop_group_scope_invalid", expected_path)


@pytest.mark.parametrize(
    "template",
    (
        "printf ok # $LOOP_PREV.producer.outputx",
        r"printf '%s' \$LOOP_PREV.producer.outputx",
    ),
    ids=("comment", "escaped-literal"),
)
def test_v6_ignores_malformed_previous_text_outside_bash_reference_contexts(
    tmp_path, workflow_writer, template
) -> None:
    path = workflow_writer(
        tmp_path,
        nodes=[
            _group(
                [{"id": "producer", "prompt": "produce"}],
                until_bash=template,
            )
        ],
    )

    assert _load_v6(path).definition.nodes[0].node_type == "loop_group"


@pytest.mark.parametrize(
    ("surface", "template"),
    [
        ("prompt", "$LOOP_PREV.producer.output,"),
        ("script", "print('$LOOP_PREV.producer.output;')"),
        ("until_bash", "printf '%s' \"$LOOP_PREV.producer.output,\""),
    ],
)
def test_v6_accepts_previous_reference_adjacent_punctuation(
    tmp_path, workflow_writer, surface, template
) -> None:
    body = [{"id": "producer", "prompt": "produce"}]
    group_options = {"max_iterations": 1}
    if surface == "until_bash":
        group_options["until_bash"] = template
    else:
        body.append({
            "id": "consumer",
            "depends_on": ["producer"],
            surface: template,
            **({"runtime": "uv"} if surface == "script" else {}),
        })
    path = workflow_writer(tmp_path, nodes=[_group(body, **group_options)])

    assert _load_v6(path).definition.nodes[0].node_type == "loop_group"


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


def test_v6_mixed_references_keep_exact_missing_dependency_semantic_code(
    tmp_path, workflow_writer
):
    path = workflow_writer(
        tmp_path,
        nodes=[
            _group([
                {"id": "producer", "prompt": "produce"},
                {
                    "id": "consumer",
                    "prompt": (
                        "Use $LOOP_PREV.producer.output and $producer.output"
                    ),
                },
            ])
        ],
    )

    with pytest.raises(WorkflowValidationError) as raised:
        _normalize_v6_without_admission(path)

    assert [
        (issue.code, getattr(issue, "semantic_code", None))
        for issue in raised.value.issues
    ] == [
        (
            "loop_group_scope_invalid",
            "scoped-reference-missing-dependency",
        )
    ]


def test_v6_admits_previous_iteration_reference_in_body_condition(
    tmp_path, workflow_writer
) -> None:
    path = workflow_writer(
        tmp_path,
        nodes=[
            _group([
                {"id": "produce", "prompt": "produce"},
                {
                    "id": "consume",
                    "depends_on": ["produce"],
                    "when": "$LOOP_PREV.produce.output == ''",
                    "prompt": "consume",
                },
            ])
        ],
    )

    package = _load_v6(path)

    group = package.definition.nodes[0]
    assert group.value["nodes"][1].options["when"] == (
        "$LOOP_PREV.produce.output == ''"
    )


@pytest.mark.parametrize(
    ("nodes", "expected_code", "expected_path"),
    [
        (
            [
                {"id": "outer", "prompt": "outer"},
                {
                    **_group([
                        {
                            "id": "consume",
                            "when": "$LOOP_PREV.outer.output == ''",
                            "prompt": "consume",
                        }
                    ]),
                    "depends_on": ["outer"],
                },
            ],
            "loop_group_scope_invalid",
            "nodes[1].loop_group.nodes[0].when",
        ),
        (
            [
                _group([
                    {
                        "id": "consume",
                        "when": "$later.output == 'ready'",
                        "prompt": "consume",
                    },
                    {"id": "later", "prompt": "later"},
                ])
            ],
            "loop_group_scope_invalid",
            "nodes[0].loop_group.nodes[0].when",
        ),
    ],
    ids=("previous-outer", "current-forward"),
)
def test_v6_rejects_invalid_previous_condition_scopes(
    tmp_path,
    workflow_writer,
    nodes,
    expected_code,
    expected_path,
) -> None:
    path = workflow_writer(tmp_path, nodes=nodes)

    _assert_issue(path, expected_code, expected_path)


def test_v6_top_level_condition_does_not_gain_previous_iteration_scope(
    tmp_path, workflow_writer
) -> None:
    path = workflow_writer(
        tmp_path,
        nodes=[
            {"id": "produce", "prompt": "produce"},
            {
                "id": "consume",
                "depends_on": ["produce"],
                "when": "$LOOP_PREV.produce.output == ''",
                "prompt": "consume",
            },
        ],
    )

    with pytest.raises(WorkflowValidationError) as raised:
        _load_v6(path)

    assert raised.value.issues[0].path == "nodes[1].when"


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
