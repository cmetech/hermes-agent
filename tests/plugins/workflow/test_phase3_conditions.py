from __future__ import annotations

import hashlib
import json
import math

import pytest

from plugins.workflow.admission import RunAdmissionRequest
from plugins.workflow.conditions import (
    WorkflowConditionError,
    evaluate_v3_condition,
    validate_v3_condition_syntax,
)
from plugins.workflow.executors.base import NodeExecutionResult
from plugins.workflow.output_resolution import (
    ResolvedNodeOutput,
    WorkflowOutputReferenceError,
)
from plugins.workflow.schema import load_workflow
from plugins.workflow.scheduler import RunScheduler, evaluate_condition
from plugins.workflow.store import RunStore
from plugins.workflow.models import WorkflowValidationError


def _resolved(
    value: object,
    *,
    node_id: str = "source",
    structured: bool = True,
    text: str | None = None,
) -> ResolvedNodeOutput:
    if text is None:
        text = (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            if structured
            else str(value)
        )
    canonical = text.encode("utf-8")
    return ResolvedNodeOutput(
        canonical_bytes=canonical,
        value=value,
        text=text,
        media_type=(
            "application/json"
            if structured
            else "text/markdown; charset=utf-8"
        ),
        sha256=hashlib.sha256(canonical).hexdigest(),
        node_id=node_id,
        attempt_id="attempt-winner",
        publication_id="a" * 32,
        schema_fingerprint="b" * 64 if structured else None,
    )


@pytest.mark.parametrize(
    ("expression", "expected"),
    (
        ("$source.output == 1", True),
        ("$source.output != 2", True),
        ("$source.output < 2", True),
        ("$source.output <= 1", True),
        ("$source.output > 0", True),
        ("$source.output >= 1", True),
        (" \t$source.output == 1\r\n", True),
        ("$source.output > '0.5'", True),
        ("$source.output > \"0.5\"", True),
        (
            "$source.output == 1 || $missing.output == 1 && "
            "$also_missing.output == 1",
            True,
        ),
        (
            "$source.output == 2 && $missing.output == 1 || "
            "$source.output == 1",
            True,
        ),
    ),
)
def test_v3_condition_grammar_operators_precedence_and_short_circuit(
    expression: str, expected: bool
) -> None:
    """Catch wrong operators, precedence, or eager evaluation in the v3 parser."""
    assert validate_v3_condition_syntax(expression)
    assert evaluate_v3_condition(expression, {"source": _resolved(1)}) is expected


@pytest.mark.parametrize(
    "expression",
    (
        "",
        "   ",
        "$source.output",
        "($source.output == 1)",
        "truthy($source.output)",
        "$source.output + 1 == 2",
        "$source.output == 1 trailing",
        "$source.output === 1",
        "$source.output == true",
        "$source.output == 1e3",
        "$source.output == 0x10",
        "$source.output == 1,000",
        "$source.output == 'unterminated",
        "$source.output == 1 and $source.output == 1",
        "$source.output.01 == 1",
        "$source.output == 1\u00a0",
    ),
)
def test_v3_condition_parser_rejects_non_grammar_forms(expression: str) -> None:
    """Catch accidental growth into truthiness, functions, or general evaluation."""
    with pytest.raises(WorkflowConditionError) as exc:
        validate_v3_condition_syntax(expression)

    assert exc.value.code == "condition_runtime_syntax_invalid"
    assert len(str(exc.value).encode("utf-8")) <= 2_000


def test_v3_condition_parser_enforces_byte_and_token_bounds() -> None:
    """Catch unbounded quoted operands and condition-token fanout."""
    oversized = "$source.output == '" + ("é" * 8_192) + "'"
    too_many_tokens = " || ".join("$source.output == 1" for _ in range(65))

    for expression in (oversized, too_many_tokens):
        with pytest.raises(WorkflowConditionError) as exc:
            validate_v3_condition_syntax(expression)
        assert exc.value.code == "condition_runtime_syntax_invalid"


def test_v3_condition_admission_uses_the_bounded_parser(
    tmp_path, workflow_writer
) -> None:
    """Catch schema admission retaining an unbounded regex-only condition path."""
    too_many_tokens = " || ".join("$source.output == 1" for _ in range(65))
    path = workflow_writer(
        tmp_path,
        nodes=[
            {"id": "source", "bash": "printf 1"},
            {
                "id": "consumer",
                "bash": "true",
                "depends_on": ["source"],
                "when": too_many_tokens,
            },
        ],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )

    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow(path)

    assert exc.value.issues[0].code == "malformed_condition"


@pytest.mark.parametrize(
    ("value", "structured", "expression", "expected"),
    (
        ("release", True, "$source.output == 'release'", True),
        ("release", True, "$source.output != 'draft'", True),
        ("", True, "$source.output == ''", True),
        (2, True, "$source.output == 2", True),
        (2, True, "$source.output > '1.5'", True),
        (0.1, True, "$source.output == .1", True),
        (9_007_199_254_740_993, True, "$source.output == 9007199254740993", True),
        (" 2.50\t", False, "$source.output == 2.5", True),
        (" 2.50\t", False, "$source.output > '2.49'", True),
        ("2", False, "$source.output == '2'", True),
        (-0.5, True, "$source.output >= -.5", True),
    ),
)
def test_v3_condition_typed_comparison_matrix(
    value: object,
    structured: bool,
    expression: str,
    expected: bool,
) -> None:
    """Catch coercion drift between canonical typed and schemaless values."""
    assert (
        evaluate_v3_condition(
            expression,
            {"source": _resolved(value, structured=structured)},
        )
        is expected
    )


@pytest.mark.parametrize(
    ("value", "structured", "expression", "code"),
    (
        ("2", True, "$source.output == 2", "condition_operand_type"),
        ("2", True, "$source.output > '1'", "condition_operand_type"),
        (2, True, "$source.output == '2'", "condition_operand_type"),
        (True, True, "$source.output == 1", "condition_operand_type"),
        (None, True, "$source.output == 'x'", "condition_operand_type"),
        ([1], True, "$source.output == 1", "condition_operand_type"),
        ({"x": 1}, True, "$source.output == 1", "condition_operand_type"),
        ("1e3", False, "$source.output > 2", "condition_numeric_invalid"),
        ("1,000", False, "$source.output > 2", "condition_numeric_invalid"),
        ("0x10", False, "$source.output > 2", "condition_numeric_invalid"),
        ("", False, "$source.output > 2", "condition_numeric_invalid"),
        ("2 trailing", False, "$source.output > 1", "condition_numeric_invalid"),
        (2, True, "$source.output > 'NaN'", "condition_numeric_invalid"),
        (2, True, "$source.output > 'Infinity'", "condition_numeric_invalid"),
    ),
)
def test_v3_condition_rejects_coercion_and_non_decimal_operands(
    value: object,
    structured: bool,
    expression: str,
    code: str,
) -> None:
    """Catch Phase 2 truthiness and string-to-number coercion leaking into v3."""
    with pytest.raises(WorkflowConditionError) as exc:
        evaluate_v3_condition(
            expression,
            {"source": _resolved(value, structured=structured)},
        )

    assert exc.value.code == code


def test_v3_condition_rejects_nonfinite_canonical_numeric_operand() -> None:
    """Catch non-finite canonical values reaching Decimal ordering."""
    output = _resolved(1)
    object.__setattr__(output, "value", math.inf)

    with pytest.raises(WorkflowConditionError) as exc:
        evaluate_v3_condition("$source.output > 1", {"source": output})

    assert exc.value.code == "condition_operand_nonfinite"


def test_v3_condition_reuses_strict_reference_failures() -> None:
    """Catch reference failures being collapsed into condition false."""
    missing = WorkflowOutputReferenceError(
        "output_reference_integrity", "source", ()
    )
    with pytest.raises(WorkflowOutputReferenceError) as exc:
        evaluate_v3_condition("$source.output == 1", {"source": missing})
    assert exc.value is missing

    with pytest.raises(WorkflowOutputReferenceError) as absent:
        evaluate_v3_condition("$absent.output == 1", {})
    assert absent.value.code == "output_reference_missing"


def _start_archon_run(tmp_path, workflow_writer, *, name: str, nodes) -> tuple[RunStore, str]:
    package_path = workflow_writer(tmp_path / name, name=name, nodes=nodes)
    package_path.with_name(f"{package_path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow(package_path)
    store = RunStore(tmp_path / f"home-{name}")
    prepared = store.prepare_run_snapshot(package)
    admitted = store.start_run(
        RunAdmissionRequest(
            workflow_name=package.definition.name,
            definition_digest=prepared.definition_digest,
            policy_digest=prepared.policy_digest,
            input_manifest_digest=prepared.input_manifest_digest,
            trigger_source="cli",
            idempotency_key=name,
            concurrency_key=name,
        ),
        immutable_snapshot=prepared,
    )
    assert admitted.run_id is not None
    return store, admitted.run_id


def test_store_atomically_fails_v3_condition_without_an_attempt(
    tmp_path, workflow_writer
) -> None:
    """Catch claim creation or retry charge in the pending-condition failure CAS."""
    store, run_id = _start_archon_run(
        tmp_path,
        workflow_writer,
        name="condition-store-failure",
        nodes=[
            {"id": "source", "bash": "true"},
            {
                "id": "consumer",
                "bash": "true",
                "depends_on": ["source"],
            },
        ],
    )

    changed = store.transition_v3_condition_node(
        run_id,
        "consumer",
        state="failed",
        code="condition_operand_type",
        message="condition operand has the wrong canonical type",
    )
    projection = store.load_run(run_id)

    assert changed is True
    assert projection["nodes"]["consumer"]["state"] == "failed"
    assert projection["nodes"]["consumer"]["attempts"] == []
    assert projection["nodes"]["consumer"]["retry_consumed"] == 0
    assert projection["last_error"] == {
        "code": "condition_operand_type",
        "message": "condition operand has the wrong canonical type",
        "node_id": "consumer",
    }
    assert store.transition_v3_condition_node(
        run_id,
        "consumer",
        state="failed",
        code="condition_operand_type",
        message="condition operand has the wrong canonical type",
    ) is False
    (store.run_directory(run_id) / "run.json").unlink()
    rebuilt = store.load_run(run_id)
    assert rebuilt["nodes"]["consumer"]["attempts"] == []
    assert rebuilt["nodes"]["consumer"]["retry_consumed"] == 0
    assert rebuilt["last_error"]["code"] == "condition_operand_type"


def test_v3_false_condition_skips_without_claim_or_retry(
    tmp_path, workflow_writer
) -> None:
    """Catch false v3 conditions consuming an executor or retry attempt."""
    store, run_id = _start_archon_run(
        tmp_path,
        workflow_writer,
        name="condition-false",
        nodes=[
            {"id": "source", "bash": "printf 1"},
            {
                "id": "consumer",
                "bash": "false",
                "depends_on": ["source"],
                "when": "$source.output > 5",
                "retry": {"max_attempts": 2, "on_error": "all"},
            },
        ],
    )

    result = RunScheduler(store).advance(run_id, max_nodes=10)

    consumer = result["nodes"]["consumer"]
    assert result["status"] == "succeeded"
    assert consumer["state"] == "skipped"
    assert consumer["skip_reason"] == "condition_false"
    assert consumer["attempts"] == []
    assert consumer["retry_consumed"] == 0


def test_v3_typed_condition_error_fails_without_claim_or_retry(
    tmp_path, workflow_writer
) -> None:
    """Catch typed errors being skipped or routed through on_error retries."""
    store, run_id = _start_archon_run(
        tmp_path,
        workflow_writer,
        name="condition-error",
        nodes=[
            {"id": "source", "bash": "printf not-a-number"},
            {
                "id": "consumer",
                "bash": "false",
                "depends_on": ["source"],
                "when": "$source.output > 5",
                "retry": {"max_attempts": 2, "on_error": "all"},
            },
        ],
    )

    result = RunScheduler(store).advance(run_id, max_nodes=10)

    consumer = result["nodes"]["consumer"]
    assert result["status"] == "failed"
    assert consumer["state"] == "failed"
    assert consumer["attempts"] == []
    assert consumer["retry_consumed"] == 0
    assert result["last_error"]["code"] == "condition_numeric_invalid"
    assert len(result["last_error"]["message"].encode("utf-8")) <= 2_000
    assert any(
        event["event_type"] == "node_failed"
        and event["node_id"] == "consumer"
        for event in store.tail_events(run_id)
    )


def test_v3_condition_reference_error_fails_before_consumer_executor(
    tmp_path, workflow_writer
) -> None:
    """Catch a strict reference failure falling through to the consumer executor."""
    store, run_id = _start_archon_run(
        tmp_path,
        workflow_writer,
        name="condition-reference-error",
        nodes=[
            {"id": "source", "bash": "true"},
            {
                "id": "consumer",
                "bash": "false",
                "depends_on": ["source"],
                "when": "$source.output == 'ready'",
                "retry": {"max_attempts": 2, "on_error": "all"},
            },
        ],
    )
    executed: list[str] = []

    class MissingOutputExecutor:
        def execute(self, context):
            executed.append(context.node.id)
            return NodeExecutionResult("succeeded")

    scheduler = RunScheduler(store)
    scheduler.executors["bash"] = MissingOutputExecutor()
    result = scheduler.advance(run_id, max_nodes=10)

    consumer = result["nodes"]["consumer"]
    assert result["status"] == "failed"
    assert consumer["state"] == "failed"
    assert consumer["attempts"] == []
    assert consumer["retry_consumed"] == 0
    assert result["last_error"]["code"] == "output_reference_missing"
    assert executed == ["source"]


def test_legacy_condition_adapter_keeps_json_reparse_and_type_behavior() -> None:
    """Catch v3 dispatch changing the frozen legacy condition adapter."""
    outputs = {"source": '{"count":2,"kind":"release"}'}

    assert evaluate_condition("$source.output.count == 2", outputs)
    assert evaluate_condition("$source.output.kind == 'release'", outputs)
