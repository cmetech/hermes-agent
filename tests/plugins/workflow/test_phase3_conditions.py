from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

from plugins.workflow import scheduler as scheduler_module
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
from plugins.workflow.schema import load_workflow, load_workflow_snapshot
from plugins.workflow.scheduler import RunScheduler, evaluate_condition
from plugins.workflow.store import ArtifactRef, RunStore
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
        (" 2.50\t", False, "$source.output == 2.5", "condition_operand_type"),
        ("2.50", False, "$source.output != 2.500", "condition_operand_type"),
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


@pytest.mark.parametrize(
    ("operator", "left", "right", "expected"),
    (
        ("==", "release", "release", True),
        ("!=", "release", "release", False),
        ("==", "release", "draft", False),
        ("!=", "release", "draft", True),
        ("==", 1, 1, True),
        ("==", 1.0, 1.0, True),
        ("==", True, True, True),
        ("==", None, None, True),
        ("==", 1, 1.0, False),
        ("!=", 1, 1.0, True),
        ("==", 1, True, False),
        ("!=", 1, True, True),
    ),
)
def test_v3_condition_compares_output_references_as_exact_json_scalars(
    operator: str,
    left: object,
    right: object,
    expected: bool,
) -> None:
    """Catch value-only equality coercing distinct canonical JSON scalar types."""
    outputs = {
        "left": _resolved({"branch": left}, node_id="left"),
        "right": _resolved({"branch": right}, node_id="right"),
    }

    assert evaluate_v3_condition(
        f"$left.output.branch {operator} $right.output.branch", outputs
    ) is expected


def test_v3_condition_validation_returns_both_operand_references_in_source_order() -> None:
    """Catch the RHS escaping dependency and structured-path compiler checks."""
    expression = "$created.output.branch == $approved.output.branch"

    references = validate_v3_condition_syntax(expression)

    assert [
        (reference.node_id, reference.path, expression[reference.start : reference.end])
        for reference in references
    ] == [
        ("created", ("branch",), "$created.output.branch"),
        ("approved", ("branch",), "$approved.output.branch"),
    ]


@pytest.mark.parametrize("operator", ("<", "<=", ">", ">="))
def test_v3_condition_rejects_ordered_output_reference_comparisons(
    operator: str,
) -> None:
    """Catch output references broadening into ordered cross-output comparisons."""
    with pytest.raises(WorkflowConditionError) as exc:
        validate_v3_condition_syntax(
            f"$left.output.branch {operator} $right.output.branch"
        )

    assert exc.value.code == "condition_runtime_syntax_invalid"


@pytest.mark.parametrize(
    ("right", "code"),
    (
        (None, "output_reference_missing"),
        (
            WorkflowOutputReferenceError(
                "output_reference_temporarily_unavailable", "right"
            ),
            "output_reference_temporarily_unavailable",
        ),
        (
            WorkflowOutputReferenceError("output_reference_unavailable", "right"),
            "output_reference_unavailable",
        ),
    ),
)
def test_v3_condition_rhs_reuses_bounded_output_availability_failures(
    right: object,
    code: str,
) -> None:
    """Catch missing or unpublished RHS outputs being collapsed to condition false."""
    with pytest.raises(WorkflowOutputReferenceError) as exc:
        evaluate_v3_condition(
            "$left.output == $right.output",
            {"left": _resolved("release", node_id="left"), "right": right},
        )

    assert exc.value.code == code
    assert len(str(exc.value).encode("utf-8")) <= 2_000


@pytest.mark.parametrize(
    ("right", "expression", "code"),
    (
        (
            _resolved("release", node_id="right", structured=False),
            "$left.output.branch == $right.output.branch",
            "output_reference_not_structured",
        ),
        (
            _resolved({"other": "release"}, node_id="right"),
            "$left.output.branch == $right.output.branch",
            "output_reference_field_missing",
        ),
    ),
)
def test_v3_condition_rhs_reuses_bounded_output_path_failures(
    right: ResolvedNodeOutput,
    expression: str,
    code: str,
) -> None:
    """Catch invalid RHS paths being traversed or reported with operand values."""
    with pytest.raises(WorkflowOutputReferenceError) as exc:
        evaluate_v3_condition(
            expression,
            {
                "left": _resolved({"branch": "release"}, node_id="left"),
                "right": right,
            },
        )

    assert exc.value.code == code
    assert "release" not in str(exc.value)


@pytest.mark.parametrize("right", (["release"], {"branch": "release"}))
def test_v3_condition_rejects_container_rhs_without_deep_comparison(
    right: object,
) -> None:
    """Catch recursive equality admitting unbounded canonical containers."""
    with pytest.raises(WorkflowConditionError) as exc:
        evaluate_v3_condition(
            "$left.output == $right.output",
            {
                "left": _resolved("release", node_id="left"),
                "right": _resolved(right, node_id="right"),
            },
        )

    assert exc.value.code == "condition_operand_type"


def test_v3_condition_rejects_corrupted_nonfinite_rhs() -> None:
    """Catch corrupted RHS floats reaching equality or diagnostic rendering."""
    right = _resolved(1, node_id="right")
    object.__setattr__(right, "value", math.nan)

    with pytest.raises(WorkflowConditionError) as exc:
        evaluate_v3_condition(
            "$left.output == $right.output",
            {"left": _resolved(1, node_id="left"), "right": right},
        )

    assert exc.value.code == "condition_operand_nonfinite"


def test_v3_condition_reference_rhs_preserves_logical_short_circuit() -> None:
    """Catch unreachable RHS-reference clauses performing output resolution."""
    resolved_nodes: list[str] = []
    outputs = {
        "left": _resolved("release", node_id="left"),
        "right": _resolved("release", node_id="right"),
    }

    def resolve(node_id: str) -> object:
        resolved_nodes.append(node_id)
        if node_id not in outputs:
            raise AssertionError("short-circuited reference was resolved")
        return outputs[node_id]

    assert evaluate_v3_condition(
        "$left.output == $right.output || $missing.output == $also_missing.output",
        resolve,
    )
    assert resolved_nodes == ["left", "right"]


def test_v3_condition_compiler_rejects_rhs_outside_direct_dependencies(
    tmp_path, workflow_writer
) -> None:
    """Catch static condition validation checking only the left reference."""
    path = workflow_writer(
        tmp_path,
        nodes=[
            {"id": "created", "bash": "printf release"},
            {"id": "approved", "bash": "printf release"},
            {
                "id": "write",
                "bash": "true",
                "depends_on": ["created"],
                "when": "$created.output == $approved.output",
            },
        ],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )

    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow_snapshot(
            path,
            workflow_bytes=path.read_bytes(),
            sidecar_bytes=path.with_name(f"{path.stem}.hermes.yaml").read_bytes(),
            normalizer_version=3,
        )

    assert [issue.code for issue in exc.value.issues] == [
        "output_reference_not_declared_dependency"
    ]


def test_v3_condition_compiler_rejects_impossible_rhs_structured_path(
    tmp_path, workflow_writer
) -> None:
    """Catch RHS field paths bypassing the producer's structured output contract."""
    output_format = {
        "type": "object",
        "properties": {"branch": {"type": "string"}},
        "required": ["branch"],
        "additionalProperties": False,
    }
    path = workflow_writer(
        tmp_path,
        nodes=[
            {
                "id": "created",
                "prompt": "create",
                "output_format": output_format,
            },
            {
                "id": "approved",
                "prompt": "approve",
                "output_format": output_format,
            },
            {
                "id": "write",
                "bash": "true",
                "depends_on": ["created", "approved"],
                "when": "$created.output.branch == $approved.output.missing",
            },
        ],
    )
    path.with_name(f"{path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )

    with pytest.raises(WorkflowValidationError) as exc:
        load_workflow_snapshot(
            path,
            workflow_bytes=path.read_bytes(),
            sidecar_bytes=path.with_name(f"{path.stem}.hermes.yaml").read_bytes(),
            normalizer_version=3,
        )

    assert [issue.code for issue in exc.value.issues] == [
        "structured_output_field_impossible"
    ]


def _start_archon_run(tmp_path, workflow_writer, *, name: str, nodes) -> tuple[RunStore, str]:
    package_path = workflow_writer(tmp_path / name, name=name, nodes=nodes)
    package_path.with_name(f"{package_path.stem}.hermes.yaml").write_text(
        "language_compatibility: archon-2026-07\n", encoding="utf-8"
    )
    package = load_workflow_snapshot(
        package_path,
        workflow_bytes=package_path.read_bytes(),
        sidecar_bytes=package_path.with_name(
            f"{package_path.stem}.hermes.yaml"
        ).read_bytes(),
        normalizer_version=3,
    )
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


@pytest.mark.parametrize(
    ("message", "expected"),
    (
        ("a" * 2_000, "a" * 2_000),
        ("a" * 2_001, "a" * 2_000),
        ("é" * 1_000, "é" * 1_000),
        ("é" * 1_001, "é" * 1_000),
        (("a" * 1_999) + "é", "a" * 1_999),
    ),
    ids=(
        "ascii-exact",
        "ascii-overflow",
        "multibyte-exact",
        "multibyte-overflow",
        "split-codepoint",
    ),
)
def test_store_bounds_v3_condition_diagnostics_by_utf8_bytes_and_rebuilds(
    tmp_path, workflow_writer, message: str, expected: str
) -> None:
    """Catch character-based persistence bounds or split UTF-8 code points."""
    store, run_id = _start_archon_run(
        tmp_path,
        workflow_writer,
        name=f"condition-diagnostic-{len(message)}-{len(message.encode('utf-8'))}",
        nodes=[
            {"id": "source", "bash": "true"},
            {"id": "consumer", "bash": "true", "depends_on": ["source"]},
        ],
    )

    assert store.transition_v3_condition_node(
        run_id,
        "consumer",
        state="failed",
        code="condition_operand_type",
        message=message,
    )
    projection = store.load_run(run_id)
    failed_event = next(
        event
        for event in store.tail_events(run_id)
        if event["event_type"] == "node_failed"
    )

    assert projection["last_error"]["message"] == expected
    assert failed_event["payload"]["error_message"] == expected
    assert len(expected.encode("utf-8")) <= 2_000
    (store.run_directory(run_id) / "run.json").unlink()
    rebuilt = store.load_run(run_id)
    assert rebuilt["last_error"]["message"] == expected
    assert len(rebuilt["last_error"]["message"].encode("utf-8")) <= 2_000


def test_store_rejects_invalid_unicode_v3_condition_diagnostic(
    tmp_path, workflow_writer
) -> None:
    """Catch lone surrogates reaching JSON journal serialization."""
    store, run_id = _start_archon_run(
        tmp_path,
        workflow_writer,
        name="condition-diagnostic-invalid-unicode",
        nodes=[
            {"id": "source", "bash": "true"},
            {"id": "consumer", "bash": "true", "depends_on": ["source"]},
        ],
    )

    with pytest.raises(ValueError, match="valid UTF-8"):
        store.transition_v3_condition_node(
            run_id,
            "consumer",
            state="failed",
            code="condition_operand_type",
            message="invalid-\ud800-diagnostic",
        )

    projection = store.load_run(run_id)
    assert projection["nodes"]["consumer"]["state"] == "pending"
    assert not any(
        event["event_type"] == "node_failed"
        for event in store.tail_events(run_id)
    )


def _complete_condition_source(
    store: RunStore,
    run_id: str,
    node_id: str,
    content: bytes,
) -> None:
    claim = store.claim_node(run_id, node_id, "condition-test-owner")
    assert claim is not None
    store.mark_node_started(claim)
    relative_path = Path("nodes") / node_id / claim.attempt_id / "stdout.log"
    output_path = store.run_directory(run_id) / relative_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(content)
    store.complete_node(
        claim,
        status="succeeded",
        artifacts=(
            ArtifactRef(
                relative_path=relative_path.as_posix(),
                media_type="text/plain; charset=utf-8",
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            ),
        ),
    )


@pytest.mark.parametrize(
    ("expression", "expected_state"),
    (
        ("$left.output == '1' || $right.output == '1'", "ready"),
        ("$left.output == '2' && $right.output == '1'", "skipped"),
    ),
)
def test_v3_scheduler_does_not_resolve_short_circuited_condition_reference(
    tmp_path,
    workflow_writer,
    monkeypatch,
    expression: str,
    expected_state: str,
) -> None:
    """Catch scheduler I/O and cache effects for an unreachable condition clause."""
    store, run_id = _start_archon_run(
        tmp_path,
        workflow_writer,
        name=f"condition-lazy-{expected_state}",
        nodes=[
            {"id": "left", "bash": "printf 1"},
            {"id": "right", "bash": "printf 1"},
            {
                "id": "consumer",
                "bash": "true",
                "depends_on": ["left", "right"],
                "when": expression,
            },
        ],
    )
    _complete_condition_source(store, run_id, "left", b"1")
    _complete_condition_source(store, run_id, "right", b"1")
    scheduler = RunScheduler(store)
    resolved_nodes: list[str] = []

    real_resolve = scheduler_module.resolve_node_output

    def recording_resolve_node_output(**kwargs):
        node_id = kwargs["node_id"]
        resolved_nodes.append(node_id)
        if node_id == "right":
            raise AssertionError("short-circuited RHS was resolved")
        return real_resolve(**kwargs)

    monkeypatch.setattr(
        scheduler_module, "resolve_node_output", recording_resolve_node_output
    )

    scheduler._resolve_graph(
        run_id,
        load_workflow(
            store.run_directory(run_id) / "definition.yaml"
        ).definition.nodes,
    )

    projection = store.load_run(run_id)
    assert projection["nodes"]["consumer"]["state"] == expected_state, (
        projection.get("last_error"),
        resolved_nodes,
    )
    assert resolved_nodes == ["left"]
    assert not any(key[2] == "right" for key in scheduler._resolved_output_cache)


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


@pytest.mark.parametrize(
    ("operator", "approved", "expected_state", "expected_attempts"),
    (
        ("==", "release", "succeeded", 1),
        ("==", "approved", "skipped", 0),
        ("!=", "release", "skipped", 0),
        ("!=", "approved", "succeeded", 1),
    ),
)
def test_v3_scheduler_durably_dispatches_from_direct_output_comparison(
    tmp_path,
    workflow_writer,
    operator: str,
    approved: str,
    expected_state: str,
    expected_attempts: int,
) -> None:
    """Catch compiler/runtime disagreement or non-durable condition transitions."""
    store, run_id = _start_archon_run(
        tmp_path,
        workflow_writer,
        name=f"condition-reference-{operator.replace('!', 'not')}-{approved}",
        nodes=[
            {"id": "created", "bash": "printf release"},
            {"id": "approved", "bash": f"printf {approved}"},
            {
                "id": "write",
                "bash": "true",
                "depends_on": ["created", "approved"],
                "when": f"$created.output {operator} $approved.output",
            },
        ],
    )

    result = RunScheduler(store).advance(run_id, max_nodes=10)

    write = result["nodes"]["write"]
    assert result["status"] == "succeeded"
    assert write["state"] == expected_state
    assert len(write["attempts"]) == expected_attempts
    if expected_state == "skipped":
        assert write["skip_reason"] == "condition_false"
        assert any(
            event["event_type"] == "node_skipped"
            and event["node_id"] == "write"
            for event in store.tail_events(run_id)
        )
    else:
        assert any(
            event["event_type"] == "node_succeeded"
            and event["node_id"] == "write"
            for event in store.tail_events(run_id)
        )

    (store.run_directory(run_id) / "run.json").unlink()
    rebuilt = store.load_run(run_id)
    assert rebuilt["nodes"]["write"]["state"] == expected_state
    assert len(rebuilt["nodes"]["write"]["attempts"]) == expected_attempts


def test_legacy_condition_adapter_keeps_json_reparse_and_type_behavior() -> None:
    """Catch v3 dispatch changing the frozen legacy condition adapter."""
    outputs = {"source": '{"count":2,"kind":"release"}'}

    assert evaluate_condition("$source.output.count == 2", outputs)
    assert evaluate_condition("$source.output.kind == 'release'", outputs)
