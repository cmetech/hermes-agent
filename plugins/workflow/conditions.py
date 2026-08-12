"""Bounded typed condition evaluation for Archon v3 workflows."""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import math
import re
from typing import Callable, Mapping

from plugins.workflow.language_schema import (
    ARCHON_V3_CONDITION_COMPARISON_OPERATORS,
    ARCHON_V3_CONDITION_DIAGNOSTIC_MAX_BYTES,
    ARCHON_V3_CONDITION_EQUALITY_OPERATORS,
    ARCHON_V3_CONDITION_LOGICAL_OPERATORS,
    ARCHON_V3_CONDITION_MAX_BYTES,
    ARCHON_V3_CONDITION_MAX_NESTING,
    ARCHON_V3_CONDITION_MAX_TOKENS,
    ARCHON_V3_CONDITION_ORDERED_OPERATORS,
    ARCHON_V3_DECIMAL_NUMBER_PATTERN,
    OutputReferenceToken,
    WorkflowReferenceSyntaxError,
    iter_output_references,
)
from plugins.workflow.output_resolution import (
    ResolvedNodeOutput,
    WorkflowOutputReferenceError,
    resolve_output_reference,
)


_ASCII_WHITESPACE = frozenset(" \t\r\n")
_DECIMAL_TOKEN = re.compile(ARCHON_V3_DECIMAL_NUMBER_PATTERN, re.ASCII)
_DECIMAL_NUMBER = re.compile(
    rf"^(?:{ARCHON_V3_DECIMAL_NUMBER_PATTERN})$", re.ASCII
)
_ERROR_MEANINGS = {
    "condition_operand_type": "condition operands have incompatible canonical types",
    "condition_operand_nonfinite": "condition numeric operand is not finite",
    "condition_numeric_invalid": "condition numeric text is not an exact decimal",
    "condition_runtime_syntax_invalid": "condition does not match the sealed v3 grammar",
}


class WorkflowConditionError(ValueError):
    """One stable failure from the Archon v3 condition boundary."""

    def __init__(self, code: str) -> None:
        if code not in _ERROR_MEANINGS:
            raise ValueError("unknown workflow condition error code")
        self.code = code
        message = f"{code}: {_ERROR_MEANINGS[code]}"
        if len(message.encode("utf-8")) > ARCHON_V3_CONDITION_DIAGNOSTIC_MAX_BYTES:
            message = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class _Literal:
    text: str
    quoted: bool


@dataclass(frozen=True, slots=True)
class _Clause:
    reference: OutputReferenceToken
    operator: str
    right: _Literal | OutputReferenceToken


@dataclass(frozen=True, slots=True)
class _Expression:
    or_groups: tuple[tuple[_Clause, ...], ...]

    @property
    def references(self) -> tuple[OutputReferenceToken, ...]:
        references: list[OutputReferenceToken] = []
        for group in self.or_groups:
            for clause in group:
                references.append(clause.reference)
                if isinstance(clause.right, OutputReferenceToken):
                    references.append(clause.right)
        return tuple(references)


class _Parser:
    def __init__(self, expression: str) -> None:
        if not isinstance(expression, str):
            raise WorkflowConditionError("condition_runtime_syntax_invalid")
        try:
            size = len(expression.encode("utf-8"))
        except UnicodeError as exc:
            raise WorkflowConditionError(
                "condition_runtime_syntax_invalid"
            ) from exc
        if not expression or size > ARCHON_V3_CONDITION_MAX_BYTES:
            raise WorkflowConditionError("condition_runtime_syntax_invalid")
        self.expression = expression
        self.position = 0
        self.token_count = 0
        self.nesting = 0

    def parse(self) -> _Expression:
        self._enter()
        try:
            self._skip_whitespace()
            groups = [self._parse_and_group()]
            while self._consume(ARCHON_V3_CONDITION_LOGICAL_OPERATORS[1]):
                self._count_token()
                groups.append(self._parse_and_group())
            self._skip_whitespace()
            if self.position != len(self.expression):
                self._syntax_error()
            return _Expression(tuple(groups))
        finally:
            self._leave()

    def _parse_and_group(self) -> tuple[_Clause, ...]:
        self._enter()
        try:
            clauses = [self._parse_clause()]
            while self._consume(ARCHON_V3_CONDITION_LOGICAL_OPERATORS[0]):
                self._count_token()
                clauses.append(self._parse_clause())
            return tuple(clauses)
        finally:
            self._leave()

    def _parse_clause(self) -> _Clause:
        self._enter()
        try:
            self._skip_whitespace()
            reference = self._parse_reference()
            self._skip_whitespace()
            operator = next(
                (
                    candidate
                    for candidate in ARCHON_V3_CONDITION_COMPARISON_OPERATORS
                    if self.expression.startswith(candidate, self.position)
                ),
                None,
            )
            if operator is None:
                self._syntax_error()
            self.position += len(operator)
            self._count_token()
            self._skip_whitespace()
            if self.position < len(self.expression) and self.expression[
                self.position
            ] == "$":
                if operator not in ARCHON_V3_CONDITION_EQUALITY_OPERATORS:
                    self._syntax_error()
                right: _Literal | OutputReferenceToken = self._parse_reference()
            else:
                right = self._parse_literal()
            self._skip_whitespace()
            return _Clause(reference, operator, right)
        finally:
            self._leave()

    def _parse_reference(self) -> OutputReferenceToken:
        suffix = self.expression[self.position :]
        try:
            reference = next(
                iter_output_references(suffix, normalizer_version=3), None
            )
        except WorkflowReferenceSyntaxError as exc:
            raise WorkflowConditionError(
                "condition_runtime_syntax_invalid"
            ) from exc
        if reference is None or reference.start != 0:
            self._syntax_error()
        absolute = OutputReferenceToken(
            node_id=reference.node_id,
            path=reference.path,
            start=self.position,
            end=self.position + reference.end,
        )
        self.position = absolute.end
        self._count_token()
        return absolute

    def _parse_literal(self) -> _Literal:
        if self.position >= len(self.expression):
            self._syntax_error()
        quote = self.expression[self.position]
        if quote in {"'", '"'}:
            end = self.expression.find(quote, self.position + 1)
            if end < 0:
                self._syntax_error()
            value = self.expression[self.position + 1 : end]
            self.position = end + 1
            self._count_token()
            return _Literal(value, True)
        match = _DECIMAL_TOKEN.match(self.expression, self.position)
        if match is None:
            self._syntax_error()
        value = match.group(0)
        self.position = match.end()
        self._count_token()
        return _Literal(value, False)

    def _consume(self, token: str) -> bool:
        self._skip_whitespace()
        if not self.expression.startswith(token, self.position):
            return False
        self.position += len(token)
        self._skip_whitespace()
        return True

    def _skip_whitespace(self) -> None:
        while (
            self.position < len(self.expression)
            and self.expression[self.position] in _ASCII_WHITESPACE
        ):
            self.position += 1

    def _count_token(self) -> None:
        self.token_count += 1
        if self.token_count > ARCHON_V3_CONDITION_MAX_TOKENS:
            self._syntax_error()

    def _enter(self) -> None:
        self.nesting += 1
        if self.nesting > ARCHON_V3_CONDITION_MAX_NESTING:
            self._syntax_error()

    def _leave(self) -> None:
        self.nesting -= 1

    @staticmethod
    def _syntax_error() -> None:
        raise WorkflowConditionError("condition_runtime_syntax_invalid")


def _parse(expression: str) -> _Expression:
    return _Parser(expression).parse()


def validate_v3_condition_syntax(
    expression: str,
) -> tuple[OutputReferenceToken, ...]:
    """Validate one v3 condition without evaluating it."""
    return _parse(expression).references


def _decimal_text(value: str) -> Decimal:
    normalized = value.strip(" \t\r\n")
    if _DECIMAL_NUMBER.fullmatch(normalized) is None:
        raise WorkflowConditionError("condition_numeric_invalid")
    try:
        parsed = Decimal(normalized)
    except InvalidOperation as exc:
        raise WorkflowConditionError("condition_numeric_invalid") from exc
    if not parsed.is_finite():
        raise WorkflowConditionError("condition_numeric_invalid")
    return parsed


def _numeric_left(value: object, *, schemaless_text: bool) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise WorkflowConditionError("condition_operand_type")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise WorkflowConditionError("condition_operand_nonfinite")
        return Decimal(str(value))
    if isinstance(value, str) and schemaless_text:
        return _decimal_text(value)
    raise WorkflowConditionError("condition_operand_type")


def _resolve_reference(
    reference: OutputReferenceToken,
    output_for: Callable[[str], object],
) -> tuple[object, bool]:
    raw_output = output_for(reference.node_id)
    if isinstance(raw_output, WorkflowOutputReferenceError):
        raise raw_output
    if raw_output is not None and not isinstance(raw_output, ResolvedNodeOutput):
        raise WorkflowConditionError("condition_operand_type")
    if (
        isinstance(raw_output, ResolvedNodeOutput)
        and not reference.path
        and isinstance(raw_output.value, float)
        and not math.isfinite(raw_output.value)
    ):
        # Canonical JSON admission rejects this value. Keep the condition
        # boundary defensive so a corrupted in-memory value cannot reach
        # Decimal or become a generic rendering failure.
        raise WorkflowConditionError("condition_operand_nonfinite")
    resolved = resolve_output_reference(
        raw_output,
        node_id=reference.node_id,
        path=reference.path,
    )
    left = resolved.typed_value
    schemaless_text = (
        isinstance(raw_output, ResolvedNodeOutput)
        and raw_output.schema_fingerprint is None
        and not reference.path
    )
    return left, schemaless_text


def _canonical_scalar(value: object) -> tuple[str, object]:
    if value is None:
        return "null", None
    if type(value) is bool:
        return "boolean", value
    if type(value) is int:
        return "integer", value
    if type(value) is float:
        if not math.isfinite(value):
            raise WorkflowConditionError("condition_operand_nonfinite")
        return "number", value
    if type(value) is str:
        return "string", value
    raise WorkflowConditionError("condition_operand_type")


def _evaluate_clause(
    clause: _Clause,
    output_for: Callable[[str], object],
) -> bool:
    left, schemaless_text = _resolve_reference(clause.reference, output_for)

    if isinstance(clause.right, OutputReferenceToken):
        right, _ = _resolve_reference(clause.right, output_for)
        equal = _canonical_scalar(left) == _canonical_scalar(right)
        return equal if clause.operator == "==" else not equal

    if (
        clause.operator in ARCHON_V3_CONDITION_EQUALITY_OPERATORS
        and clause.right.quoted
    ):
        if not isinstance(left, str):
            raise WorkflowConditionError("condition_operand_type")
        equal = left == clause.right.text
        return equal if clause.operator == "==" else not equal

    left_decimal = _numeric_left(
        left,
        schemaless_text=(
            schemaless_text
            and clause.operator in ARCHON_V3_CONDITION_ORDERED_OPERATORS
        ),
    )
    right_decimal = _decimal_text(clause.right.text)
    operations = {
        "==": left_decimal == right_decimal,
        "!=": left_decimal != right_decimal,
        "<": left_decimal < right_decimal,
        "<=": left_decimal <= right_decimal,
        ">": left_decimal > right_decimal,
        ">=": left_decimal >= right_decimal,
    }
    return operations[clause.operator]


def evaluate_v3_condition(
    expression: str,
    outputs: Mapping[str, object] | Callable[[str], object],
) -> bool:
    """Evaluate one v3 condition against canonical node outputs."""
    parsed = _parse(expression)
    output_for = outputs.get if isinstance(outputs, Mapping) else outputs
    for group in parsed.or_groups:
        group_matches = True
        for clause in group:
            if not _evaluate_clause(clause, output_for):
                group_matches = False
                break
        if group_matches:
            return True
    return False


__all__ = [
    "WorkflowConditionError",
    "evaluate_v3_condition",
    "validate_v3_condition_syntax",
]
