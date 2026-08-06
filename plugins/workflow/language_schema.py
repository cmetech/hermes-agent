"""Dependency-neutral workflow field inventory and authoring schemas."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from hashlib import sha256
import json
import math
import re
from types import MappingProxyType
from typing import Any

from plugins.workflow.language import (
    CURRENT_NORMALIZER_BY_PROFILE,
    DYNAMIC_LANGUAGE_COMPATIBILITY_CODES,
    SUPPORTED_NORMALIZER_VERSIONS,
    supports_phase3_semantics,
)
from plugins.workflow.models import WorkflowLanguageProfile


_DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"
_PROFILES = tuple(WorkflowLanguageProfile)
MAX_WORKFLOW_DOCUMENT_BYTES = 2 * 1024 * 1024
DURABLE_METADATA_STRING_MAX_CHARS = 16_384
ARCHON_V3_CONDITION_MAX_BYTES = 16_384
ARCHON_V3_CONDITION_MAX_TOKENS = 256
ARCHON_V3_CONDITION_MAX_NESTING = 3
ARCHON_V3_CONDITION_DIAGNOSTIC_MAX_BYTES = 2_000
ARCHON_V3_CONDITION_EQUALITY_OPERATORS = ("==", "!=")
ARCHON_V3_CONDITION_ORDERED_OPERATORS = ("<=", ">=", "<", ">")
ARCHON_V3_CONDITION_COMPARISON_OPERATORS = (
    *ARCHON_V3_CONDITION_EQUALITY_OPERATORS,
    *ARCHON_V3_CONDITION_ORDERED_OPERATORS,
)
ARCHON_V3_CONDITION_LOGICAL_OPERATORS = ("&&", "||")
ARCHON_V3_CONDITION_PRECEDENCE = (
    (("&&",), "left", ("||",)),
    (("||",), "left", ()),
)
ARCHON_V3_CONDITION_EVALUATION_ORDER = "left_to_right"
ARCHON_V3_CONDITION_SHORT_CIRCUIT = True
BASH_INLINE_MAX_BYTES = 32_768
BASH_RENDERED_COMMAND_MAX_BYTES = 96 * 1024
BASH_SPILL_MAX_FILES = 64
BASH_SPILL_MAX_VALUE_BYTES = 500_000
BASH_SPILL_MAX_TOTAL_BYTES = 2_000_000
ARCHON_V3_CONDITION_TYPED_OPERAND_MODES = MappingProxyType({
    "quoted_equality": "exact_string_only",
    "unquoted_decimal_equality": "canonical_finite_number_only",
    "ordered_lhs": (
        "canonical_finite_number",
        "schemaless_whole_decimal_text",
    ),
    "ordered_rhs": ("unquoted_decimal", "quoted_decimal"),
    "structured_strings_coerce_to_number": False,
})
CONTRACT_READER_VERSION = 2
EDITOR_PROJECTION_VERSION = 2
CONTRACT_MAX_BYTES = 256_000
CONTRACT_RESERVED_GROWTH_BYTES = 4_000
CONTRACT_SECTION_MAX_BYTES = MappingProxyType({
    "definition_schema": 150_000,
    "node_kinds": 72_000,
    "compatibility_codes": 15_000,
})
_NO_DEFAULT = object()
WHEN_REFERENCE_PATTERN = r"\$([\w.:-]+)\.output(?:\.[\w.-]+)*"
ARCHON_V3_NODE_ID_PATTERN = r"[A-Za-z_][A-Za-z0-9_-]*"
ARCHON_V3_OUTPUT_PATH_SEGMENT_PATTERN = (
    r"(?:[A-Za-z_][A-Za-z0-9_-]*|0|[1-9][0-9]*)"
)
ARCHON_V3_OUTPUT_REFERENCE_PATTERN = (
    rf"\$(?P<node>{ARCHON_V3_NODE_ID_PATTERN})\.output"
    rf"(?P<path>(?:\.{ARCHON_V3_OUTPUT_PATH_SEGMENT_PATTERN})*)"
)
ARCHON_V3_DECIMAL_NUMBER_PATTERN = r"-?(?:\d+(?:\.\d*)?|\.\d+)"
ECMASCRIPT_ARCHON_V3_OUTPUT_REFERENCE_PATTERN = (
    rf"\$({ARCHON_V3_NODE_ID_PATTERN})\.output"
    rf"(?:\.{ARCHON_V3_OUTPUT_PATH_SEGMENT_PATTERN})*"
)
ARCHON_V3_WHEN_CLAUSE_PATTERN = (
    rf"{ECMASCRIPT_ARCHON_V3_OUTPUT_REFERENCE_PATTERN}\s*"
    r"(?:==|!=|<=|>=|<|>)\s*"
    rf"(?:'[^']*'|\"[^\"]*\"|{ARCHON_V3_DECIMAL_NUMBER_PATTERN})"
)
ARCHON_V3_WHEN_EXPRESSION_PATTERN = (
    rf"^\s*{ARCHON_V3_WHEN_CLAUSE_PATTERN}"
    rf"(?:\s*(?:&&|\|\|)\s*{ARCHON_V3_WHEN_CLAUSE_PATTERN})*\s*$"
)
WHEN_CLAUSE_PATTERN = (
    r"\$[\w.:-]+\.output(?:\.[\w.-]+)*\s*"
    r"(?:==|!=|<=|>=|<|>)\s*"
    r"(?:'[^']*'|\"[^\"]*\"|-?(?:\d+(?:\.\d*)?|\.\d+))"
)
WHEN_EXPRESSION_PATTERN = (
    rf"^\s*{WHEN_CLAUSE_PATTERN}"
    rf"(?:\s*(?:&&|\|\|)\s*{WHEN_CLAUSE_PATTERN})*\s*$"
)
ECMASCRIPT_WHEN_REFERENCE_PATTERN = (
    r"\$([\p{L}\p{N}_.:-]+)\.output(?:\.[\p{L}\p{N}_.-]+)*"
)
ECMASCRIPT_WHEN_CLAUSE_PATTERN = (
    r"\$[\p{L}\p{N}_.:-]+\.output(?:\.[\p{L}\p{N}_.-]+)*\s*"
    r"(?:==|!=|<=|>=|<|>)\s*"
    r"""(?:'[^']*'|"[^"]*"|-?(?:\d+(?:\.\d*)?|\.\d+))"""
)
ECMASCRIPT_WHEN_EXPRESSION_PATTERN = (
    rf"^\s*{ECMASCRIPT_WHEN_CLAUSE_PATTERN}"
    rf"(?:\s*(?:&&|\|\|)\s*{ECMASCRIPT_WHEN_CLAUSE_PATTERN})*\s*$"
)


@dataclass(frozen=True, slots=True)
class OutputReferenceToken:
    """One exact versioned workflow output reference in an authored surface."""

    node_id: str
    path: tuple[str, ...]
    start: int
    end: int


class WorkflowReferenceSyntaxError(ValueError):
    """A reference-like token cannot be represented by the v3 grammar."""

    code = "output_reference_path_unsupported"

    def __init__(self, message: str, *, start: int | None = None) -> None:
        self.start = start
        super().__init__(message)


_ARCHON_V3_NODE_ID = re.compile(rf"^(?:{ARCHON_V3_NODE_ID_PATTERN})$", re.ASCII)
_ARCHON_V3_OUTPUT_REFERENCE = re.compile(
    ARCHON_V3_OUTPUT_REFERENCE_PATTERN, re.ASCII
)
_ARCHON_V3_WHEN_OPERATOR = re.compile(r"(?:==|!=|<=|>=|<|>)", re.ASCII)
_ARCHON_V3_WHEN_NUMBER = re.compile(
    ARCHON_V3_DECIMAL_NUMBER_PATTERN, re.ASCII
)
_REFERENCE_CANDIDATE_END = frozenset(" \t\r\n'\"(){}<>=!&|,;:")


def _require_strict_reference_semantics(normalizer_version: int) -> None:
    if (
        isinstance(normalizer_version, bool)
        or normalizer_version not in SUPPORTED_NORMALIZER_VERSIONS
        or not supports_phase3_semantics(
            WorkflowLanguageProfile.ARCHON_2026_07,
            normalizer_version,
        )
    ):
        raise ValueError(
            "strict output references require inherited Phase 3 semantics"
        )


def is_reference_safe_node_id(value: str) -> bool:
    """Return whether a node ID is addressable by the Archon v3 grammar."""
    return bool(_ARCHON_V3_NODE_ID.fullmatch(value))


def _reference_candidate_end(template: str, start: int) -> int:
    end = start + 1
    while end < len(template) and template[end] not in _REFERENCE_CANDIDATE_END:
        end += 1
    return end


def _bash_reference_candidate_end(template: str, start: int) -> int:
    """Bound one shell candidate without swallowing a following dollar token."""
    end = start + 1
    while (
        end < len(template)
        and template[end] != "$"
        and template[end] not in _REFERENCE_CANDIDATE_END
    ):
        end += 1
    return end


def _reference_like_candidate(template: str, start: int, end: int) -> bool:
    """Recognize a possible output token without applying the strict grammar."""
    if start + 1 >= end:
        return False
    first = template[start + 1]
    if not (first == "_" or first.isalnum() or not first.isascii()):
        return False
    candidate = template[start:end]
    return ".output" in candidate or bool(
        re.search(r"[./\\]output(?:[.\[\]/\\]|$)", candidate, re.ASCII)
    )


def iter_output_reference_candidate_spans(
    template: str,
    *,
    normalizer_version: int,
) -> Iterator[tuple[int, int]]:
    """Yield reference-like dollar ranges without rejecting their grammar."""
    _require_strict_reference_semantics(normalizer_version)
    position = 0
    while True:
        start = template.find("$", position)
        if start < 0:
            return
        end = _bash_reference_candidate_end(template, start)
        if _reference_like_candidate(template, start, end):
            yield start, end
        # Inspect nested dollars independently (for ${...}, $[], $(), and
        # ANSI-C quote contexts) rather than trusting the outer shell token.
        position = start + 1


def iter_output_references_in_spans(
    template: str,
    spans: Iterable[tuple[int, int]],
    *,
    normalizer_version: int,
) -> Iterator[OutputReferenceToken]:
    """Apply the strict grammar only to lexically admitted candidate spans."""
    _require_strict_reference_semantics(normalizer_version)
    previous_end = 0
    for start, end in spans:
        if start < previous_end or start < 0 or end <= start or end > len(template):
            raise ValueError("output reference candidate spans are invalid")
        previous_end = end
        candidate = template[start:end]
        try:
            token = _output_reference_at(candidate, 0)
        except WorkflowReferenceSyntaxError as exc:
            local_start = exc.start if exc.start is not None else 0
            raise WorkflowReferenceSyntaxError(
                str(exc),
                start=start + local_start,
            ) from exc
        if token is not None:
            yield OutputReferenceToken(
                node_id=token.node_id,
                path=token.path,
                start=start + token.start,
                end=start + token.end,
            )


def _complete_reference_at(template: str, start: int) -> bool:
    match = _ARCHON_V3_OUTPUT_REFERENCE.match(template, start)
    if match is None:
        return False
    following = template[match.end()] if match.end() < len(template) else ""
    return not following or not (
        following in ".[\\/-"
        or following == "_"
        or following.isalnum()
        or not following.isascii()
    )


def _output_reference_at(
    template: str, start: int
) -> OutputReferenceToken | None:
    match = _ARCHON_V3_OUTPUT_REFERENCE.match(template, start)
    if match is not None:
        end = match.end()
        if not _complete_reference_at(template, start):
            raise WorkflowReferenceSyntaxError(
                "output reference uses an unsupported path",
                start=start,
            )
        raw_path = match.group("path")
        return OutputReferenceToken(
            node_id=match.group("node"),
            path=tuple(raw_path[1:].split(".")) if raw_path else (),
            start=start,
            end=end,
        )
    candidate_end = _reference_candidate_end(template, start)
    candidate = template[start:candidate_end]
    if ".output" in candidate or re.search(
        r"[./\\]output(?:[.\[\]/\\]|$)", candidate, re.ASCII
    ):
        raise WorkflowReferenceSyntaxError(
            "output reference uses an unsupported path",
            start=start,
        )
    return None


def iter_output_references(
    template: str,
    *,
    normalizer_version: int,
) -> Iterator[OutputReferenceToken]:
    """Iterate references with the single ASCII grammar used by Archon v3."""
    _require_strict_reference_semantics(normalizer_version)
    position = 0
    while True:
        start = template.find("$", position)
        if start < 0:
            return
        token = _output_reference_at(template, start)
        if token is not None:
            yield token
            position = token.end
            continue
        candidate_end = _reference_candidate_end(template, start)
        position = max(start + 1, candidate_end)


def contains_output_reference(
    template: str,
    *,
    normalizer_version: int,
) -> bool:
    """Find any complete v3 reference despite other malformed candidates."""
    _require_strict_reference_semantics(normalizer_version)
    position = 0
    while True:
        start = template.find("$", position)
        if start < 0:
            return False
        if _complete_reference_at(template, start):
            return True
        position = start + 1


def iter_when_output_references(
    expression: str,
    *,
    normalizer_version: int,
) -> Iterator[OutputReferenceToken]:
    """Yield only v3 condition operands; quoted RHS text stays literal."""
    _require_strict_reference_semantics(normalizer_version)
    position = 0
    while position < len(expression) and expression[position].isspace():
        position += 1
    while position < len(expression):
        token = _output_reference_at(expression, position)
        if token is None:
            return
        position = token.end
        while position < len(expression) and expression[position].isspace():
            position += 1
        operator = _ARCHON_V3_WHEN_OPERATOR.match(expression, position)
        if operator is None:
            return
        position = operator.end()
        while position < len(expression) and expression[position].isspace():
            position += 1
        if position >= len(expression):
            return
        quote = expression[position]
        if quote in "'\"":
            closing = expression.find(quote, position + 1)
            if closing < 0:
                return
            position = closing + 1
        else:
            number = _ARCHON_V3_WHEN_NUMBER.match(expression, position)
            if number is None:
                return
            position = number.end()
        yield token
        while position < len(expression) and expression[position].isspace():
            position += 1
        if expression.startswith("&&", position) or expression.startswith(
            "||", position
        ):
            position += 2
            while position < len(expression) and expression[position].isspace():
                position += 1
            continue
        break


@dataclass(frozen=True, slots=True)
class FieldCompatibility:
    """One field's stable authoring status for a language profile."""

    profile: WorkflowLanguageProfile
    status: str
    code: str | None


@dataclass(frozen=True, slots=True)
class WorkflowFieldSpec:
    """Immutable syntax and compatibility authority for one YAML field."""

    scope: str
    yaml_name: str
    json_type: str
    shape: str
    structural_node_types: frozenset[str]
    applicable_node_types: frozenset[str]
    enforcement_phase: int
    compatibility: tuple[FieldCompatibility, ...]
    required: bool
    required_node_types: frozenset[str]
    title: str
    description: str
    widget: str
    section: str
    examples: tuple[object, ...]
    value_role: str | None
    default_value: object
    pattern: str | None
    max_length: int | None


@dataclass(frozen=True, slots=True)
class StructuralRequirement:
    """A JSON-Schema requirement derived from loader structure."""

    scope: str
    when_field: str
    equals: object
    required_field: str
    required_shape: str


@dataclass(frozen=True, slots=True)
class DurableWorkflowCode:
    """Bounded public metadata for one versioned durable workflow code."""

    code: str
    public_meaning: str
    area: str
    profiles: frozenset[WorkflowLanguageProfile]
    normalizer_versions: frozenset[int]
    compatibility: bool
    runtime_failure: bool
    evidence: bool
    fields: tuple[str, ...]

    @property
    def minimum_normalizer_version(self) -> int:
        """Return the earliest sealed language version that can emit this code."""
        return min(self.normalizer_versions)

    @property
    def effective_profile(self) -> str:
        """Return the sole profile covered by a durable language code."""
        if len(self.profiles) != 1:
            raise ValueError("durable code must name exactly one effective profile")
        return next(iter(self.profiles)).value

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "public_meaning": self.public_meaning,
            "area": self.area,
            "profiles": sorted(profile.value for profile in self.profiles),
            "normalizer_versions": sorted(self.normalizer_versions),
            "compatibility": self.compatibility,
            "runtime_failure": self.runtime_failure,
            "evidence": self.evidence,
            "fields": list(self.fields),
        }


_ARCHON_V3 = frozenset({WorkflowLanguageProfile.ARCHON_2026_07})
_NORMALIZER_V3 = frozenset({3})
_NORMALIZER_V4 = frozenset({4})
# The projected Phase 3 code catalog is an authenticated/API-facing bounded
# summary. 16 KiB covers the approved normalization/reference/condition codes
# plus the remaining planned Bash and session-recovery entries without making
# each additive task revise an unrelated test ceiling.
PHASE3_DURABLE_CODE_CATALOG_MAX_BYTES = 16 * 1024


PHASE3_DURABLE_CODES = (
    DurableWorkflowCode(
        "archon_timeout_node_unsupported",
        "timeout is not supported on this node kind",
        "normalization",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].timeout",),
    ),
    DurableWorkflowCode(
        "archon_idle_timeout_node_unsupported",
        "idle_timeout is not supported on this node kind",
        "normalization",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].idle_timeout",),
    ),
    DurableWorkflowCode(
        "archon_retry_node_unsupported",
        "retry is not supported on this node kind",
        "normalization",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].retry",),
    ),
    DurableWorkflowCode(
        "archon_retry_max_attempts_required",
        "deterministic retries require max_attempts",
        "normalization",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].retry.max_attempts",),
    ),
    DurableWorkflowCode(
        "archon_timeout_invalid",
        "timeout must be a positive finite millisecond number",
        "normalization",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].timeout",),
    ),
    DurableWorkflowCode(
        "archon_idle_timeout_invalid",
        "idle_timeout must be a positive finite millisecond number",
        "normalization",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].idle_timeout",),
    ),
    DurableWorkflowCode(
        "archon_retry_invalid",
        "retry must use the bounded Archon v3 shape",
        "normalization",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].retry",),
    ),
    DurableWorkflowCode(
        "workflow_language_snapshot_mismatch",
        "sealed language semantics differ from the normalized package",
        "normalization",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("resources.language",),
    ),
    DurableWorkflowCode(
        "workflow_execution_semantics_mismatch",
        "sealed execution semantics are malformed or differ from the normalized request",
        "normalization",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("resources.phase3_execution_semantics",),
    ),
    DurableWorkflowCode(
        "archon_node_id_not_reference_safe",
        "node id cannot be represented by the Archon v3 reference grammar",
        "normalization",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].id",),
    ),
    DurableWorkflowCode(
        "output_reference_not_declared_dependency",
        "referenced producer is not a direct declared dependency",
        "references",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        (
            "nodes[].when",
            "nodes[].prompt",
            "nodes[].bash",
            "nodes[].script",
            "nodes[].command",
            "nodes[].loop",
            "nodes[].approval",
        ),
    ),
    DurableWorkflowCode(
        "output_reference_path_unsupported",
        "output reference path cannot be represented or addressed",
        "references",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        (
            "nodes[].when",
            "nodes[].prompt",
            "nodes[].bash",
            "nodes[].script",
            "nodes[].command",
            "nodes[].loop",
            "nodes[].approval",
        ),
    ),
    DurableWorkflowCode(
        "structured_output_field_impossible",
        "declared structured output schema excludes the referenced path",
        "references",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        (
            "nodes[].when",
            "nodes[].prompt",
            "nodes[].bash",
            "nodes[].script",
            "nodes[].command",
            "nodes[].loop",
            "nodes[].approval",
        ),
    ),
    DurableWorkflowCode(
        "named_script_output_reference_unsupported",
        "named scripts cannot interpolate workflow output references",
        "references",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].script",),
    ),
    DurableWorkflowCode(
        "invalid_command_resource",
        "authenticated command bytes cannot be decoded and parsed safely",
        "references",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].command",),
    ),
    DurableWorkflowCode(
        "output_reference_missing",
        "declared producer has no successful winning output",
        "references",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].output references",),
    ),
    DurableWorkflowCode(
        "output_reference_not_structured",
        "field access requires a declared structured output",
        "references",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].output references",),
    ),
    DurableWorkflowCode(
        "output_reference_field_missing",
        "referenced structured field or index is absent",
        "references",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].output references",),
    ),
    DurableWorkflowCode(
        "output_reference_path_type",
        "reference path cannot descend through the canonical value",
        "references",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].output references",),
    ),
    DurableWorkflowCode(
        "output_reference_integrity",
        "winning output publication identity or content changed",
        "references",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].output references",),
    ),
    DurableWorkflowCode(
        "output_reference_temporarily_unavailable",
        "winning output is temporarily unavailable to the host reader",
        "references",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].output references",),
    ),
    DurableWorkflowCode(
        "output_reference_unavailable",
        "bounded output-resolution reads were exhausted",
        "references",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].output references",),
    ),
    DurableWorkflowCode(
        "condition_operand_type",
        "condition operands have incompatible canonical types",
        "conditions",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].when",),
    ),
    DurableWorkflowCode(
        "condition_operand_nonfinite",
        "condition numeric operand is not finite",
        "conditions",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].when",),
    ),
    DurableWorkflowCode(
        "condition_numeric_invalid",
        "condition numeric text is not an exact finite decimal",
        "conditions",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].when",),
    ),
    DurableWorkflowCode(
        "condition_runtime_syntax_invalid",
        "sealed condition no longer matches the admitted v3 grammar",
        "conditions",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].when",),
    ),
    DurableWorkflowCode(
        "bash_substitution_nul",
        "Bash substitution contains a NUL byte that shell variables cannot carry",
        "bash",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].bash",),
    ),
    DurableWorkflowCode(
        "bash_substitution_limit",
        "Bash substitutions exceed a bounded count or byte limit",
        "bash",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].bash",),
    ),
    DurableWorkflowCode(
        "bash_spill_integrity",
        "Bash spill descriptor materialization or launch failed integrity checks",
        "bash",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].bash",),
    ),
    DurableWorkflowCode(
        "bash_reference_context_unsupported",
        "Bash reference appears in a shell context that cannot be rewritten safely",
        "bash",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].bash",),
    ),
    DurableWorkflowCode(
        "context_missing_session",
        "same-run shared context has no resumable provider session",
        "sessions",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("nodes[].context",),
    ),
    DurableWorkflowCode(
        "persistent_session_recovery_unavailable",
        "persistent session state could not be verified safely",
        "sessions",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        False,
        ("persist_sessions", "nodes[].persist_session"),
    ),
    DurableWorkflowCode(
        "persistent_session_missing_fresh_start",
        "confirmed missing cross-run session selected one fresh execution",
        "sessions",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        False,
        True,
        ("evidence.recovery",),
    ),
    DurableWorkflowCode(
        "persistent_session_registry_update_pending",
        "winning persistent session update requires operator retry",
        "sessions",
        _ARCHON_V3,
        _NORMALIZER_V3,
        False,
        True,
        True,
        ("evidence.recovery",),
    ),
)

# Phase 4 tasks append codes here as they add an executable emitter test.  Keep
# the registry separate from Phase 3 so its coverage cannot silently expand a
# frozen Phase 3 inventory.
PHASE4_DURABLE_CODES: tuple[DurableWorkflowCode, ...] = ()


def phase4_durable_code_catalog() -> Mapping[str, DurableWorkflowCode]:
    """Expose the real Phase 4 durable registrations for behavior coverage."""
    catalog = {code.code: code for code in PHASE4_DURABLE_CODES}
    if len(catalog) != len(PHASE4_DURABLE_CODES):
        raise RuntimeError("Phase 4 durable codes must be unique")
    return MappingProxyType(catalog)


def _compatibility(
    *,
    legacy_status: str = "supported",
    legacy_code: str | None = None,
    archon_status: str = "supported",
    archon_code: str | None = None,
) -> tuple[FieldCompatibility, ...]:
    return (
        FieldCompatibility(
            WorkflowLanguageProfile.HERMES_LEGACY,
            legacy_status,
            legacy_code,
        ),
        FieldCompatibility(
            WorkflowLanguageProfile.ARCHON_2026_07,
            archon_status,
            archon_code,
        ),
    )


def _humanize(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").strip().capitalize()


def _widget_for(scope: str, yaml_name: str, shape: str) -> str:
    if yaml_name == "output_format":
        return "json-schema"
    if yaml_name in {"bash", "script", "command"}:
        return "code"
    if yaml_name in {
        "description",
        "prompt",
        "systemPrompt",
        "when",
        "until",
        "gate_message",
        "systemMessage",
        "stopReason",
        "permissionDecisionReason",
        "additionalContext",
    }:
        return "textarea"
    if shape == "boolean":
        return "boolean"
    if shape in {
        "positive_number",
        "positive_integer",
        "retry_attempts",
        "retry_delay",
        "loop_iterations",
        "approval_attempts",
    }:
        return "number"
    if shape in {
        "effort",
        "trigger_rule",
        "context",
        "runtime",
        "retry_error",
        "hook_decision",
        "permission_decision",
        "hook_action",
        "hook_event_name",
        "language_profile",
        "execution_environment",
        "overlap_policy",
        "pause_lane_policy",
    }:
        return "enum"
    if shape in {"string_list", "hook_entries"}:
        return "array"
    if shape == "mapping":
        return "map"
    if shape in {
        "worktree",
        "thinking",
        "retry",
        "hooks",
        "hook_response",
        "hook_specific",
        "nullable_hook_specific",
        "agents",
        "loop_payload",
        "approval_payload",
        "approval_reject",
    }:
        return "object"
    if shape == "any":
        return "json-schema"
    return "text"


def _section_for(scope: str, yaml_name: str) -> str:
    if scope == "definition" and yaml_name in {"name", "description", "tags"}:
        return "General"
    if scope == "node" and yaml_name in {"id", *SOURCE_NODE_TYPES}:
        return "General"
    if scope in {"retry", "loop", "approval", "approval_reject"} or yaml_name in {
        "depends_on",
        "when",
        "trigger_rule",
        "context",
        "idle_timeout",
        "retry",
        "always_run",
        "runtime",
        "deps",
        "timeout",
    }:
        return "Execution"
    return "Advanced"


def _value_role_for(yaml_name: str) -> str | None:
    if yaml_name in {"bash", "script", "command"}:
        return "code"
    if yaml_name in {
        "description",
        "prompt",
        "systemPrompt",
        "when",
        "until",
        "gate_message",
        "systemMessage",
        "stopReason",
        "permissionDecisionReason",
        "additionalContext",
    }:
        return "multiline"
    if yaml_name == "mcp":
        return "resource-path"
    if yaml_name in {"skills", "deps"}:
        return "resource-reference"
    if yaml_name == "required_secrets":
        return "secret-reference"
    return None


def _description_for(scope: str, yaml_name: str) -> str:
    subject = _humanize(yaml_name)
    if scope == "definition":
        return f"{subject} for the workflow definition."
    if scope == "sidecar":
        return f"{subject} metadata for the optional companion document."
    if scope == "node":
        return f"{subject} for this workflow node."
    return f"{subject} within the node's {scope.replace('_', ' ')} settings."


def _field_description(
    spec: WorkflowFieldSpec,
    profile: WorkflowLanguageProfile,
) -> str:
    """Return stable prose; structured profile semantics are projected separately."""
    return spec.description


def _field_semantics(
    spec: WorkflowFieldSpec,
    profile: WorkflowLanguageProfile,
) -> dict[str, object] | None:
    if profile is not WorkflowLanguageProfile.ARCHON_2026_07:
        return None
    key = (spec.scope, spec.yaml_name)
    return {
        ("node", "timeout"): {
            "unit": "milliseconds",
            "omitted": 120_000,
            "scope": "attempt",
        },
        ("node", "idle_timeout"): {
            "unit": "milliseconds",
            "omitted": "sealed_ai_idle",
            "scope": "attempt",
        },
        ("retry", "max_attempts"): {
            "counts": "retries_after_initial",
            "omitted_ai": 2,
            "omitted_deterministic": 0,
        },
        ("node", "depends_on"): {"output_references": "direct_only"},
        ("node", "when"): {
            "operands": "typed_scalar",
            "false": "skip",
            "errors": "fail_pre_execution",
        },
        ("node", "bash"): {
            "inline_utf8_bytes": BASH_INLINE_MAX_BYTES,
            "rendered_command_utf8_bytes": BASH_RENDERED_COMMAND_MAX_BYTES,
            "spill_value_utf8_bytes": BASH_SPILL_MAX_VALUE_BYTES,
            "spill_files": BASH_SPILL_MAX_FILES,
            "spill_total_utf8_bytes": BASH_SPILL_MAX_TOTAL_BYTES,
            "large_values": "contents",
            "contexts": {
                "unquoted_token": "substitute",
                "double_quoted_token": "substitute",
                "single_quoted_token": "safe_quote_boundary",
                "escaped_or_comment": "literal",
            },
            "unsupported_context": "fail",
        },
        ("node", "persist_session"): {
            "confirmed_cross_run_missing": "one_fresh_execution"
        },
    }.get(key)


def _field_semantics_id(spec: WorkflowFieldSpec) -> str:
    """Return the stable contract-local id for one authoritative semantic record."""
    return f"{spec.scope}.{spec.yaml_name}"


def resolve_field_semantics(
    profile: WorkflowLanguageProfile,
    semantics_ref: str,
) -> dict[str, object] | None:
    """Resolve an editor semantic id through the same field-inventory authority."""
    selected = _profile(profile)
    spec = next(
        (item for item in FIELD_INVENTORY if _field_semantics_id(item) == semantics_ref),
        None,
    )
    return None if spec is None else _field_semantics(spec, selected)


def field_definition_catalog(
    profile: WorkflowLanguageProfile,
    *,
    definition_ids: frozenset[str] | None = None,
) -> dict[str, dict[str, object]]:
    """Project self-contained editor metadata keyed by unique inventory scope."""
    selected = _profile(profile)
    catalog: dict[str, dict[str, object]] = {}
    inventory_ids: set[str] = set()
    for spec in FIELD_INVENTORY:
        definition_id = _field_semantics_id(spec)
        if definition_id in inventory_ids:
            raise RuntimeError(f"duplicate workflow field definition id: {definition_id}")
        inventory_ids.add(definition_id)
        if definition_ids is not None and definition_id not in definition_ids:
            continue
        definition: dict[str, object] = {
            "label": spec.title,
            "description": _field_description(spec, selected),
            "examples": [_thaw_editor_value(example) for example in spec.examples],
            "widget": spec.widget,
            "section": spec.section,
        }
        unit = _field_unit(spec, selected)
        if unit is not None and (
            selected is WorkflowLanguageProfile.HERMES_LEGACY
            or _field_semantics(spec, selected) is not None
        ):
            definition["unit"] = unit
        semantics = _field_semantics(spec, selected)
        if semantics is not None:
            definition["semantics"] = semantics
        catalog[definition_id] = definition
    return catalog


def _freeze_editor_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze_editor_value(item) for key, item in value.items()
        })
    if isinstance(value, list | tuple):
        return tuple(_freeze_editor_value(item) for item in value)
    return value


def _thaw_editor_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _thaw_editor_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_editor_value(item) for item in value]
    return value


def _example_for(yaml_name: str, shape: str) -> object:
    named: dict[str, object] = {
        "id": "node-id",
        "name": "workflow-name",
        "description": "Describe this workflow.",
        "command": "/review",
        "prompt": "Summarize the upstream result.",
        "bash": "printf 'ok\\n'",
        "script": "print('ok')",
        "cancel": "Cancellation requested.",
        "when": "$prepare.output.status == 'ready'",
        "depends_on": ["prepare"],
        "output_format": {"type": "object"},
        "mcp": "mcp/server.yaml",
        "required_secrets": ["SERVICE_API_KEY"],
        "systemPrompt": "Follow the workflow instructions.",
        "until": "done",
        "gate_message": "Approve the next iteration.",
        "hookEventName": "PreToolUse",
    }
    if yaml_name in named:
        return named[yaml_name]
    by_shape: dict[str, object] = {
        "any": True,
        "string": "value",
        "nonempty_string": "value",
        "boolean": True,
        "positive_number": 1,
        "positive_integer": 1,
        "string_list": ["value"],
        "mapping": {"key": "value"},
        "worktree": {"enabled": True},
        "effort": "medium",
        "thinking": "adaptive",
        "trigger_rule": "all_success",
        "context": "fresh",
        "runtime": "uv",
        "retry_attempts": 2,
        "retry_delay": 1000,
        "retry_error": "transient",
        "loop_iterations": 3,
        "approval_attempts": 2,
        "hook_decision": "approve",
        "permission_decision": "allow",
        "hook_action": "accept",
        "hook_event_name": "PreToolUse",
        "language_profile": "hermes-legacy",
        "execution_environment": "trusted_local",
        "overlap_policy": "queue",
        "pause_lane_policy": "hold",
        "retry": {"max_attempts": 2},
        "hooks": {},
        "hook_entries": [{"response": {"continue": True}}],
        "hook_response": {"continue": True},
        "hook_specific": {"hookEventName": "PreToolUse"},
        "nullable_hook_specific": None,
        "agents": {
            "reviewer": {
                "description": "Review the result.",
                "prompt": "Check the output.",
            }
        },
        "nodes": [{"id": "start", "bash": "true"}],
        "loop_payload": {
            "prompt": "Try again.",
            "until": "done",
            "max_iterations": 3,
        },
        "approval_payload": {"message": "Continue?"},
        "approval_reject": {"prompt": "Try again."},
    }
    if shape in by_shape:
        return by_shape[shape]
    if shape.endswith("_payload"):
        return "value"
    raise ValueError(f"workflow field shape has no editor example: {shape}")


def _field(
    scope: str,
    yaml_name: str,
    json_type: str,
    shape: str,
    *,
    node_types: tuple[str, ...] = (),
    structural_node_types: tuple[str, ...] | None = None,
    required: bool = False,
    required_node_types: tuple[str, ...] = (),
    phase: int = 1,
    legacy_status: str = "supported",
    legacy_code: str | None = None,
    archon_status: str = "supported",
    archon_code: str | None = None,
    title: str | None = None,
    description: str | None = None,
    widget: str | None = None,
    section: str | None = None,
    examples: tuple[object, ...] | None = None,
    value_role: str | None = None,
    default_value: object = _NO_DEFAULT,
    pattern: str | None = None,
    max_length: int | None = None,
) -> WorkflowFieldSpec:
    return WorkflowFieldSpec(
        scope=scope,
        yaml_name=yaml_name,
        json_type=json_type,
        shape=shape,
        structural_node_types=frozenset(
            node_types if structural_node_types is None else structural_node_types
        ),
        applicable_node_types=frozenset(node_types),
        enforcement_phase=phase,
        compatibility=_compatibility(
            legacy_status=legacy_status,
            legacy_code=legacy_code,
            archon_status=archon_status,
            archon_code=archon_code,
        ),
        required=required,
        required_node_types=frozenset(required_node_types),
        title=title or _humanize(yaml_name),
        description=description or _description_for(scope, yaml_name),
        widget=widget or _widget_for(scope, yaml_name, shape),
        section=section or _section_for(scope, yaml_name),
        examples=tuple(
            _freeze_editor_value(example)
            for example in (examples or (_example_for(yaml_name, shape),))
        ),
        value_role=value_role or _value_role_for(yaml_name),
        default_value=default_value,
        pattern=pattern,
        max_length=max_length,
    )


EXECUTABLE_NODE_TYPES = (
    "command",
    "prompt",
    "bash",
    "script",
    "loop",
    "approval",
    "cancel",
)
COMPILE_DIRECTIVE_TYPES = ("include",)
SOURCE_NODE_TYPES = (*EXECUTABLE_NODE_TYPES, *COMPILE_DIRECTIVE_TYPES)
# Backward-compatible public inventory consumed by schedulers and compatibility
# checks. Compile directives must never enter this executable-kind tuple.
NODE_TYPES = EXECUTABLE_NODE_TYPES
_AI_NODE_TYPES = ("command", "prompt")
_NON_LOOP_NODE_TYPES = tuple(item for item in NODE_TYPES if item != "loop")
_AI_EXTENSION_NODE_OPTIONS = (
    ("mcp", "string", "nonempty_string"),
    ("skills", "array", "string_list"),
)
ARCHON_EXTENSION_EXPANSION_PHASE = 4


_DEFINITION_FIELDS = (
    _field("definition", "name", "string", "nonempty_string", required=True),
    _field("definition", "description", "string", "nonempty_string", required=True),
    _field("definition", "nodes", "array", "nodes", required=True),
    _field("definition", "provider", "string", "nonempty_string"),
    _field("definition", "model", "string", "nonempty_string"),
    _field("definition", "modelReasoningEffort", "string", "nonempty_string"),
    _field("definition", "webSearchMode", "string", "nonempty_string"),
    _field("definition", "interactive", "boolean", "boolean"),
    _field("definition", "requires", "array", "string_list"),
    _field("definition", "worktree", "object", "worktree"),
    _field("definition", "tags", "array", "string_list"),
    _field("definition", "persist_sessions", "boolean", "boolean"),
    _field("definition", "effort", "string", "effort"),
    _field("definition", "thinking", "string or object", "thinking"),
    _field("definition", "fallbackModel", "string", "nonempty_string"),
    _field("definition", "betas", "array", "string_list"),
    _field(
        "definition",
        "sandbox",
        "object",
        "mapping",
        phase=5,
        archon_status="blocking",
        archon_code="archon_sandbox_enforcement_unavailable",
    ),
)


_NODE_FIELDS = (
    _field(
        "node",
        "id",
        "string",
        "nonempty_string",
        node_types=SOURCE_NODE_TYPES,
        required_node_types=SOURCE_NODE_TYPES,
        pattern=r"^[^\s/\\]+$",
    ),
    *(
        _field(
            "node",
            node_type,
            "object" if node_type in {"loop", "approval"} else "string",
            f"{node_type}_payload",
            node_types=(node_type,),
            required_node_types=(node_type,),
        )
        for node_type in NODE_TYPES
    ),
    _field(
        "node",
        "depends_on",
        "array",
        "string_list",
        node_types=SOURCE_NODE_TYPES,
        default_value=(),
    ),
    _field("node", "when", "string", "nonempty_string", node_types=NODE_TYPES),
    _field(
        "node",
        "trigger_rule",
        "string",
        "trigger_rule",
        node_types=SOURCE_NODE_TYPES,
        default_value="all_success",
    ),
    _field("node", "context", "string", "context", node_types=NODE_TYPES),
    _field(
        "node",
        "idle_timeout",
        "number",
        "positive_number",
        node_types=NODE_TYPES,
        phase=3,
        legacy_status="warning",
        legacy_code="legacy_idle_timeout_seconds",
    ),
    _field(
        "node",
        "retry",
        "object",
        "retry",
        node_types=_NON_LOOP_NODE_TYPES,
        phase=3,
    ),
    _field("node", "always_run", "boolean", "boolean", node_types=NODE_TYPES),
    _field(
        "node",
        "output_type",
        "string",
        "nonempty_string",
        node_types=NODE_TYPES,
        phase=2,
        legacy_status="warning",
        legacy_code="legacy_output_type_not_published",
        pattern=r"\S",
        max_length=DURABLE_METADATA_STRING_MAX_CHARS,
    ),
    _field(
        "node",
        "persist_session",
        "boolean",
        "boolean",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "provider",
        "string",
        "nonempty_string",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "model",
        "string",
        "nonempty_string",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "output_format",
        "object",
        "mapping",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
        phase=2,
        legacy_status="warning",
        legacy_code="legacy_output_format_post_validation",
    ),
    _field(
        "node",
        "allowed_tools",
        "array",
        "string_list",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "denied_tools",
        "array",
        "string_list",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "hooks",
        "object",
        "hooks",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    *(
        _field(
            "node",
            name,
            json_type,
            shape,
            node_types=_AI_NODE_TYPES,
            structural_node_types=NODE_TYPES,
        )
        for name, json_type, shape in _AI_EXTENSION_NODE_OPTIONS
    ),
    _field(
        "node",
        "agents",
        "object",
        "agents",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "effort",
        "string",
        "effort",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "thinking",
        "string or object",
        "thinking",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "maxBudgetUsd",
        "number",
        "positive_number",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
        phase=5,
        archon_status="blocking",
        archon_code="archon_budget_enforcement_unavailable",
    ),
    _field(
        "node",
        "systemPrompt",
        "string",
        "nonempty_string",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "fallbackModel",
        "string",
        "nonempty_string",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "betas",
        "array",
        "string_list",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
    ),
    _field(
        "node",
        "sandbox",
        "object",
        "mapping",
        node_types=_AI_NODE_TYPES,
        structural_node_types=NODE_TYPES,
        phase=5,
        archon_status="blocking",
        archon_code="archon_sandbox_enforcement_unavailable",
    ),
    _field(
        "node",
        "runtime",
        "string",
        "runtime",
        node_types=("script",),
        required_node_types=("script",),
    ),
    _field("node", "deps", "array", "string_list", node_types=("script",)),
    _field(
        "node",
        "timeout",
        "number",
        "positive_number",
        node_types=("bash", "script"),
        phase=3,
        legacy_status="warning",
        legacy_code="legacy_timeout_seconds",
    ),
)


# Compile-only source directives are intentionally excluded from FIELD_INVENTORY,
# whose node entries describe scheduler-executable kinds and compatibility.
SOURCE_DIRECTIVE_INVENTORY = (
    _field(
        "node",
        "include",
        "string",
        "include_payload",
        node_types=("include",),
        required_node_types=("include",),
        phase=4,
        examples=("reusable-checks",),
        pattern=r"^[^\s/\\:$?#{}`()]+$",
        max_length=128,
    ),
)


_RETRY_FIELDS = (
    _field(
        "retry",
        "max_attempts",
        "integer",
        "retry_attempts",
        phase=3,
        legacy_status="warning",
        legacy_code="legacy_retry_total_attempts",
    ),
    _field("retry", "on_error", "string", "retry_error"),
    _field("retry", "delay_ms", "integer", "retry_delay"),
)
_LOOP_FIELDS = (
    _field("loop", "prompt", "string", "nonempty_string", required=True),
    _field("loop", "until", "string", "nonempty_string", required=True),
    _field("loop", "max_iterations", "integer", "loop_iterations", required=True),
    _field("loop", "fresh_context", "any", "any"),
    _field("loop", "until_bash", "any", "any"),
    _field("loop", "interactive", "any", "any"),
    _field("loop", "gate_message", "any", "any"),
)
_APPROVAL_FIELDS = (
    _field("approval", "message", "string", "nonempty_string", required=True),
    _field("approval", "capture_response", "any", "any"),
    _field("approval", "on_reject", "object", "approval_reject"),
)
_APPROVAL_REJECT_FIELDS = (
    _field("approval_reject", "prompt", "string", "nonempty_string", required=True),
    _field("approval_reject", "max_attempts", "integer", "approval_attempts"),
)
_AGENT_FIELDS = tuple(
    _field(
        "agent",
        name,
        json_type,
        shape,
        required=name in {"description", "prompt"},
    )
    for name, json_type, shape in (
        ("description", "string", "nonempty_string"),
        ("prompt", "string", "nonempty_string"),
        ("model", "string", "nonempty_string"),
        ("tools", "array", "string_list"),
        ("disallowedTools", "array", "string_list"),
        ("skills", "array", "string_list"),
        ("maxTurns", "integer", "positive_integer"),
    )
)


_HOOK_EVENT_NAMES = (
    "PreToolUse",
    "PostToolUse",
    "PostToolUseFailure",
    "Notification",
    "Stop",
    "SubagentStart",
    "SubagentStop",
    "PreCompact",
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PermissionRequest",
    "Setup",
    "TeammateIdle",
    "TaskCompleted",
    "Elicitation",
    "ElicitationResult",
    "InstructionsLoaded",
    "ConfigChange",
    "WorktreeCreate",
    "WorktreeRemove",
)
_HOOK_EVENT_FIELDS = tuple(
    _field("hook_event", event, "array", "hook_entries", node_types=_AI_NODE_TYPES)
    for event in _HOOK_EVENT_NAMES
)
_HOOK_ENTRY_FIELDS = (
    _field("hook_entry", "matcher", "any", "any"),
    _field("hook_entry", "response", "object", "hook_response", required=True),
    _field("hook_entry", "timeout", "number", "positive_number"),
)
_HOOK_RESPONSE_FIELDS = tuple(
    _field("hook_response", name, json_type, shape)
    for name, json_type, shape in (
        ("hookSpecificOutput", "object or null", "nullable_hook_specific"),
        ("systemMessage", "string", "nonempty_string"),
        ("continue", "boolean", "boolean"),
        ("decision", "string", "hook_decision"),
        ("stopReason", "string", "nonempty_string"),
        ("suppressOutput", "boolean", "boolean"),
    )
)
_HOOK_SPECIFIC_FIELDS = tuple(
    _field(
        "hook_specific",
        name,
        json_type,
        shape,
        required=name == "hookEventName",
    )
    for name, json_type, shape in (
        ("hookEventName", "string", "hook_event_name"),
        ("permissionDecision", "string", "permission_decision"),
        ("permissionDecisionReason", "string", "nonempty_string"),
        ("updatedInput", "object", "mapping"),
        ("additionalContext", "string", "nonempty_string"),
        ("updatedMCPToolOutput", "any", "any"),
        ("action", "string", "hook_action"),
        ("content", "any", "any"),
    )
)


_SIDECAR_FIELDS = tuple(
    _field(
        "sidecar",
        name,
        json_type,
        shape,
        legacy_status=("warning" if name == "language_compatibility" else "supported"),
        legacy_code=(
            "legacy_language_profile" if name == "language_compatibility" else None
        ),
    )
    for name, json_type, shape in (
        ("language_compatibility", "string", "language_profile"),
        ("delivery_defaults", "object", "mapping"),
        ("required_services", "array", "string_list"),
        ("retention", "object", "mapping"),
        ("tags", "array", "string_list"),
        ("outward_action_nodes", "array", "string_list"),
        ("outward_action_policy", "string", "nonempty_string"),
        ("execution_environment", "string", "execution_environment"),
        ("overlap_policy", "string", "overlap_policy"),
        ("pause_lane_policy", "string", "pause_lane_policy"),
        ("concurrency_key", "string", "nonempty_string"),
        ("limits", "object", "mapping"),
        ("resource_limits", "object", "mapping"),
        ("required_secrets", "array", "string_list"),
        ("scheduling", "object", "mapping"),
    )
)


FIELD_INVENTORY = (
    *_DEFINITION_FIELDS,
    *_NODE_FIELDS,
    *_RETRY_FIELDS,
    *_LOOP_FIELDS,
    *_APPROVAL_FIELDS,
    *_APPROVAL_REJECT_FIELDS,
    *_AGENT_FIELDS,
    *_HOOK_EVENT_FIELDS,
    *_HOOK_ENTRY_FIELDS,
    *_HOOK_RESPONSE_FIELDS,
    *_HOOK_SPECIFIC_FIELDS,
    *_SIDECAR_FIELDS,
)

STRUCTURAL_REQUIREMENTS = (
    StructuralRequirement(
        scope="loop",
        when_field="interactive",
        equals=True,
        required_field="gate_message",
        required_shape="json_truthy",
    ),
)


def _field_names(scope: str) -> frozenset[str]:
    return frozenset(spec.yaml_name for spec in FIELD_INVENTORY if spec.scope == scope)


def definition_field_names() -> frozenset[str]:
    return _field_names("definition")


def common_node_field_names() -> frozenset[str]:
    return _field_names("node") | frozenset(
        spec.yaml_name for spec in SOURCE_DIRECTIVE_INVENTORY
    )


def field_max_length(scope: str, yaml_name: str) -> int | None:
    """Return the canonical authored string bound for one direct field."""
    matches = tuple(
        spec
        for spec in FIELD_INVENTORY
        if spec.scope == scope and spec.yaml_name == yaml_name
    )
    if len(matches) != 1:
        raise ValueError(f"unknown or ambiguous workflow field: {scope}.{yaml_name}")
    return matches[0].max_length


def structural_node_field_names(node_type: str) -> frozenset[str]:
    """Return node fields the loader and JSON Schema accept structurally."""
    if node_type not in NODE_TYPES:
        raise ValueError(f"unsupported workflow node type: {node_type}")
    return frozenset(
        spec.yaml_name
        for spec in _specs("node")
        if node_type in spec.structural_node_types
    )


def _node_field_is_structural(
    spec: WorkflowFieldSpec,
    node_type: str,
    profile: WorkflowLanguageProfile,
) -> bool:
    if node_type not in spec.structural_node_types:
        return False
    if profile is not WorkflowLanguageProfile.ARCHON_2026_07:
        return True
    if spec.yaml_name == "idle_timeout":
        return node_type in _AI_NODE_TYPES
    if spec.yaml_name == "retry":
        return node_type in {"command", "prompt", "bash", "script"}
    return True


def inapplicable_node_fields(node_type: str) -> dict[str, frozenset[str]]:
    """Return structurally valid fields that are not semantically applicable."""
    if node_type not in NODE_TYPES:
        raise ValueError(f"unsupported workflow node type: {node_type}")
    return {
        spec.yaml_name: spec.applicable_node_types
        for spec in _specs("node")
        if node_type in spec.structural_node_types
        and node_type not in spec.applicable_node_types
    }


def sidecar_field_names() -> frozenset[str]:
    return _field_names("sidecar")


def retry_field_names() -> frozenset[str]:
    return _field_names("retry")


def loop_field_names() -> frozenset[str]:
    return _field_names("loop")


def approval_field_names() -> frozenset[str]:
    return _field_names("approval")


def approval_reject_field_names() -> frozenset[str]:
    return _field_names("approval_reject")


def agent_field_names() -> frozenset[str]:
    return _field_names("agent")


def hook_event_names() -> frozenset[str]:
    return _field_names("hook_event")


def hook_entry_field_names() -> frozenset[str]:
    return _field_names("hook_entry")


def hook_response_field_names() -> frozenset[str]:
    return _field_names("hook_response")


def hook_specific_field_names() -> frozenset[str]:
    return _field_names("hook_specific")


def _specs(scope: str) -> tuple[WorkflowFieldSpec, ...]:
    return tuple(spec for spec in FIELD_INVENTORY if spec.scope == scope)


def _profile(value: WorkflowLanguageProfile) -> WorkflowLanguageProfile:
    if not isinstance(value, WorkflowLanguageProfile):
        raise TypeError("profile must be a WorkflowLanguageProfile")
    return value


def _field_status(
    spec: WorkflowFieldSpec, profile: WorkflowLanguageProfile
) -> FieldCompatibility:
    return next(item for item in spec.compatibility if item.profile is profile)


def _editor_status(status: str) -> str:
    return "deferred" if status == "blocking" else "supported"


def _field_order(spec: WorkflowFieldSpec) -> int:
    try:
        return FIELD_INVENTORY.index(spec) + 1
    except ValueError:
        return len(FIELD_INVENTORY) + SOURCE_DIRECTIVE_INVENTORY.index(spec) + 1


def _field_unit(
    spec: WorkflowFieldSpec, profile: WorkflowLanguageProfile
) -> str | None:
    if spec.yaml_name == "delay_ms":
        return "milliseconds"
    if spec.yaml_name in {"idle_timeout", "timeout"}:
        return (
            "seconds"
            if profile is WorkflowLanguageProfile.HERMES_LEGACY
            else "milliseconds"
        )
    if spec.yaml_name == "maxBudgetUsd":
        return "USD"
    if spec.yaml_name in {"max_iterations", "max_attempts", "maxTurns"}:
        return "count"
    return None


def _compatibility_description(code: str, raw_status: str) -> str:
    return (
        f'Hermes reports compatibility code "{code}" as a {raw_status} '
        "finding for the listed fields."
    )


def _compatibility_migration(code: str) -> str:
    return (
        f'Run "hermes workflow doctor" and resolve compatibility code "{code}" '
        "before relying on this field in the selected profile."
    )


def _json_truthy_schema() -> dict[str, Any]:
    """Describe exactly the JSON values accepted by Python truth testing."""
    return {
        "oneOf": [
            {"const": True},
            {"type": "number", "not": {"const": 0}},
            {"type": "string", "minLength": 1},
            {"type": "array", "minItems": 1},
            {"type": "object", "minProperties": 1},
        ]
    }


def _schema_for_shape(
    shape: str,
    profile: WorkflowLanguageProfile,
    *,
    hook_event: str | None = None,
) -> dict[str, Any]:
    if shape == "any":
        return {}
    if shape == "json_truthy":
        return _json_truthy_schema()
    if shape == "string":
        return {"type": "string"}
    if shape == "nonempty_string":
        return {"type": "string", "minLength": 1}
    if shape == "boolean":
        return {"type": "boolean"}
    if shape == "positive_number":
        return {"type": "number", "exclusiveMinimum": 0}
    if shape == "positive_integer":
        return {"type": "integer", "minimum": 1}
    if shape == "string_list":
        return {"type": "array", "items": {"type": "string", "minLength": 1}}
    if shape == "mapping":
        return {"type": "object"}
    if shape == "worktree":
        return {
            "type": "object",
            "properties": {"enabled": {"type": "boolean"}},
            "required": ["enabled"],
            "additionalProperties": False,
        }
    if shape == "effort":
        return {"type": "string", "enum": ["low", "medium", "high", "max"]}
    if shape == "thinking":
        return {
            "oneOf": [
                {"type": "string", "enum": ["adaptive", "disabled"]},
                {
                    "type": "object",
                    "properties": {
                        "type": {"const": "enabled"},
                        "budgetTokens": {"type": "integer", "minimum": 1},
                    },
                    "required": ["type", "budgetTokens"],
                    "additionalProperties": False,
                },
            ]
        }
    if shape == "trigger_rule":
        return {
            "type": "string",
            "enum": [
                "all_success",
                "one_success",
                "none_failed_min_one_success",
                "all_done",
            ],
        }
    if shape == "context":
        return {"type": "string", "enum": ["fresh", "shared"]}
    if shape == "runtime":
        return {"type": "string", "enum": ["bun", "uv"]}
    if shape == "retry_attempts":
        return {"type": "integer", "minimum": 1, "maximum": 5}
    if shape == "retry_delay":
        return {"type": "integer", "minimum": 1000, "maximum": 60_000}
    if shape == "retry_error":
        return {"type": "string", "enum": ["transient", "all"]}
    if shape == "loop_iterations":
        return {"type": "integer", "minimum": 1, "maximum": 100}
    if shape == "approval_attempts":
        return {"type": "integer", "minimum": 1, "maximum": 10}
    if shape == "hook_decision":
        return {"type": "string", "enum": ["approve", "block"]}
    if shape == "permission_decision":
        return {"type": "string", "enum": ["deny", "allow", "ask"]}
    if shape == "hook_action":
        return {"type": "string", "enum": ["accept", "decline", "cancel"]}
    if shape == "hook_event_name":
        if hook_event is not None:
            return {"const": hook_event}
        return {"type": "string", "enum": list(_HOOK_EVENT_NAMES)}
    if shape == "language_profile":
        return {"type": "string", "enum": [item.value for item in _PROFILES]}
    if shape == "execution_environment":
        return {
            "type": "string",
            "enum": ["trusted_local", "isolated_backend_required"],
        }
    if shape == "overlap_policy":
        return {"type": "string", "enum": ["queue", "allow", "forbid"]}
    if shape == "pause_lane_policy":
        return {"type": "string", "enum": ["hold", "release"]}
    if shape == "retry":
        return _object_schema("retry", profile)
    if shape == "hooks":
        return _object_schema("hook_event", profile)
    if shape == "hook_entries":
        return {
            "type": "array",
            "minItems": 1,
            "items": _object_schema("hook_entry", profile, hook_event=hook_event),
        }
    if shape == "hook_response":
        return _object_schema("hook_response", profile, hook_event=hook_event)
    if shape == "hook_specific":
        return _object_schema("hook_specific", profile, hook_event=hook_event)
    if shape == "nullable_hook_specific":
        return {
            "oneOf": [
                {"type": "null"},
                _object_schema("hook_specific", profile, hook_event=hook_event),
            ]
        }
    if shape == "agents":
        return {
            "type": "object",
            "propertyNames": {"pattern": "^[a-z0-9]+(?:-[a-z0-9]+)*$"},
            "additionalProperties": _object_schema("agent", profile),
        }
    if shape == "nodes":
        return _nodes_schema(profile)
    if shape == "loop_payload":
        return _object_schema("loop", profile)
    if shape == "approval_payload":
        return _object_schema("approval", profile)
    if shape == "approval_reject":
        return _object_schema("approval_reject", profile)
    if shape.endswith("_payload"):
        return {"type": "string", "minLength": 1}
    raise ValueError(f"unknown workflow field shape: {shape}")


def _field_schema(
    spec: WorkflowFieldSpec,
    profile: WorkflowLanguageProfile,
    *,
    hook_event: str | None = None,
) -> dict[str, Any]:
    result = _schema_for_shape(spec.shape, profile, hook_event=hook_event)
    status = _field_status(spec, profile)
    result.update({
        "title": spec.title,
        "description": _field_description(spec, profile),
        "x-hermes-section": spec.section,
        "x-hermes-order": _field_order(spec),
        "x-hermes-widget": spec.widget,
        "x-hermes-status": status.status,
    })
    if spec.examples:
        result["examples"] = (
            [hook_event]
            if spec.shape == "hook_event_name" and hook_event is not None
            else [_thaw_editor_value(example) for example in spec.examples]
        )
    if spec.default_value is not _NO_DEFAULT:
        result["default"] = (
            list(spec.default_value)
            if isinstance(spec.default_value, tuple)
            else spec.default_value
        )
    if spec.pattern is not None:
        result["pattern"] = spec.pattern
    if (
        profile is WorkflowLanguageProfile.ARCHON_2026_07
        and spec.scope == "node"
        and spec.yaml_name == "id"
    ):
        result["pattern"] = rf"^{ARCHON_V3_NODE_ID_PATTERN}$"
    if spec.max_length is not None:
        result["maxLength"] = spec.max_length
    unit = _field_unit(spec, profile)
    if unit is not None:
        result["x-hermes-unit"] = unit
    if spec.value_role is not None:
        result["x-hermes-value-role"] = spec.value_role
    semantics = _field_semantics(spec, profile)
    if semantics is not None:
        result["x-hermes-semantics"] = semantics
    if status.status != "supported":
        result["x-hermes-compatibility-code"] = status.code
        result["x-hermes-enforcement-phase"] = spec.enforcement_phase
        if status.code is not None:
            result["x-hermes-migration"] = _compatibility_migration(status.code)
    return result


def _object_schema(
    scope: str,
    profile: WorkflowLanguageProfile,
    *,
    hook_event: str | None = None,
) -> dict[str, Any]:
    specs = _specs(scope)
    result: dict[str, Any] = {
        "type": "object",
        "properties": {
            spec.yaml_name: _field_schema(
                spec,
                profile,
                hook_event=(spec.yaml_name if scope == "hook_event" else hook_event),
            )
            for spec in specs
        },
        "additionalProperties": False,
    }
    required = tuple(spec.yaml_name for spec in specs if spec.required)
    if required:
        result["required"] = list(required)
    conditions = tuple(item for item in STRUCTURAL_REQUIREMENTS if item.scope == scope)
    if conditions:
        result["allOf"] = [
            {
                "if": {
                    "properties": {
                        item.when_field: {"const": item.equals},
                    },
                    "required": [item.when_field],
                },
                "then": {
                    "required": [item.required_field],
                    "properties": {
                        item.required_field: _schema_for_shape(
                            item.required_shape, profile
                        )
                    },
                },
            }
            for item in conditions
        ]
    return result


def _nodes_schema(profile: WorkflowLanguageProfile) -> dict[str, Any]:
    specs = (*_specs("node"), *SOURCE_DIRECTIVE_INVENTORY)
    union_properties = {spec.yaml_name: _field_schema(spec, profile) for spec in specs}
    variants = []
    for node_type in SOURCE_NODE_TYPES:
        properties = {
            spec.yaml_name: True
            for spec in specs
            if _node_field_is_structural(spec, node_type, profile)
        }
        variants.append({
            "type": "object",
            "properties": properties,
            "required": [
                spec.yaml_name
                for spec in specs
                if node_type in spec.required_node_types
            ],
            "additionalProperties": False,
        })
    return {
        "type": "array",
        "minItems": 1,
        "items": {
            "type": "object",
            "properties": union_properties,
            "oneOf": variants,
            "additionalProperties": False,
        },
    }


def definition_json_schema(
    profile: WorkflowLanguageProfile,
) -> dict[str, object]:
    """Return the deterministic definition authoring schema for ``profile``."""
    selected = _profile(profile)
    return {
        "$schema": _DRAFT_2020_12,
        "$id": f"https://hermes.local/workflow/{selected.value}/definition.schema.json",
        "title": f"Hermes workflow definition ({selected.value})",
        "description": (
            "Structural authoring schema. The workflow loader remains authoritative "
            "for graph references, resource paths, provider capabilities, and runtime "
            "compatibility checks that are not structural JSON constraints."
        ),
        "type": "object",
        "properties": {
            spec.yaml_name: _field_schema(spec, selected)
            for spec in _specs("definition")
        },
        "required": [spec.yaml_name for spec in _specs("definition") if spec.required],
        "additionalProperties": (selected is WorkflowLanguageProfile.HERMES_LEGACY),
    }


def sidecar_json_schema(profile: WorkflowLanguageProfile) -> dict[str, object]:
    """Return the strict Hermes companion-file authoring schema."""
    selected = _profile(profile)
    schema = _object_schema("sidecar", selected)
    return {
        "$schema": _DRAFT_2020_12,
        "$id": f"https://hermes.local/workflow/{selected.value}/companion.schema.json",
        "title": f"Hermes workflow companion ({selected.value})",
        **schema,
    }


def _contract_path(spec: WorkflowFieldSpec) -> str:
    if spec.scope == "definition":
        return spec.yaml_name
    if spec.scope == "node":
        return f"nodes[].{spec.yaml_name}"
    if spec.scope == "sidecar":
        return f"sidecar.{spec.yaml_name}"
    if spec.scope == "retry":
        return f"nodes[].retry.{spec.yaml_name}"
    return f"{spec.scope}.{spec.yaml_name}"


def compatibility_code_catalog(
    profile: WorkflowLanguageProfile,
) -> dict[str, object]:
    """Return stable profile-specific codes referenced by schema annotations."""
    selected = _profile(profile)
    grouped: dict[str, dict[str, object]] = {}
    for spec in FIELD_INVENTORY:
        status = _field_status(spec, selected)
        if status.code is None or status.status == "supported":
            continue
        entry = grouped.setdefault(
            status.code,
            {
                "status": _editor_status(status.status),
                "description": _compatibility_description(status.code, status.status),
                "migration": _compatibility_migration(status.code),
                "runtime_status": status.status,
                "severity": "error" if status.status == "blocking" else "warning",
                "blocking": status.status == "blocking",
                "enforcement_phase": spec.enforcement_phase,
                "fields": [],
            },
        )
        fields = entry["fields"]
        assert isinstance(fields, list)
        fields.append(_contract_path(spec))
    for spec in DYNAMIC_LANGUAGE_COMPATIBILITY_CODES:
        if selected not in spec.profiles:
            continue
        grouped[spec.code] = {
            "status": _editor_status(spec.status),
            "description": _compatibility_description(spec.code, spec.status),
            "migration": _compatibility_migration(spec.code),
            "runtime_status": spec.status,
            "severity": "error" if spec.status == "blocking" else "warning",
            "blocking": spec.status == "blocking",
            "enforcement_phase": spec.enforcement_phase,
            "fields": list(spec.fields),
        }
    for spec in (*PHASE3_DURABLE_CODES, *PHASE4_DURABLE_CODES):
        if selected not in spec.profiles:
            continue
        entry = {
            "status": "deferred",
            "description": spec.public_meaning,
            "fields": list(spec.fields),
            "area": spec.area,
            "profiles": sorted(profile.value for profile in spec.profiles),
            "normalizer_versions": sorted(spec.normalizer_versions),
            "compatibility": spec.compatibility,
            "runtime_failure": spec.runtime_failure,
            "evidence": spec.evidence,
        }
        if spec.compatibility:
            entry.update({
                "migration": _compatibility_migration(spec.code),
                "runtime_status": "blocking",
                "severity": "error",
                "blocking": True,
                "enforcement_phase": 3,
            })
        grouped[spec.code] = entry
    return {
        code: {**entry, "fields": sorted(entry["fields"])}
        for code, entry in sorted(grouped.items())
    }


def _nested_specs_for_kind(
    node_type: str, profile: WorkflowLanguageProfile,
) -> tuple[tuple[WorkflowFieldSpec, str], ...]:
    nested: list[tuple[WorkflowFieldSpec, str]] = []
    if node_type != "loop" and not (
        profile is WorkflowLanguageProfile.ARCHON_2026_07
        and node_type not in {"command", "prompt", "bash", "script"}
    ):
        nested.extend(
            (spec, f"nodes[].retry.{spec.yaml_name}") for spec in _specs("retry")
        )
    if node_type == "loop":
        nested.extend(
            (spec, f"nodes[].loop.{spec.yaml_name}") for spec in _specs("loop")
        )
    if node_type == "approval":
        nested.extend(
            (spec, f"nodes[].approval.{spec.yaml_name}") for spec in _specs("approval")
        )
        nested.extend(
            (spec, f"nodes[].approval.on_reject.{spec.yaml_name}")
            for spec in _specs("approval_reject")
        )
    if node_type in _AI_NODE_TYPES:
        nested.extend(
            (spec, f"nodes[].agents.*.{spec.yaml_name}") for spec in _specs("agent")
        )
        nested.extend(
            (spec, f"nodes[].hooks.{spec.yaml_name}") for spec in _specs("hook_event")
        )
        nested.extend(
            (spec, f"nodes[].hooks.*[].{spec.yaml_name}")
            for spec in _specs("hook_entry")
        )
        nested.extend(
            (spec, f"nodes[].hooks.*[].response.{spec.yaml_name}")
            for spec in _specs("hook_response")
        )
        nested.extend(
            (
                spec,
                f"nodes[].hooks.*[].response.hookSpecificOutput.{spec.yaml_name}",
            )
            for spec in _specs("hook_specific")
        )
    return tuple(nested)


def _field_descriptor(
    spec: WorkflowFieldSpec,
    profile: WorkflowLanguageProfile,
    node_type: str,
    field_path: str,
    *,
    parent_spec: WorkflowFieldSpec | None = None,
) -> dict[str, object]:
    status = _field_status(spec, profile)
    parent_status = (
        _field_status(parent_spec, profile) if parent_spec is not None else None
    )
    editor_status = (
        "deferred"
        if parent_status is not None
        and _editor_status(parent_status.status) == "deferred"
        else _editor_status(status.status)
    )
    descriptor: dict[str, object] = {
        "id": f"{node_type}.{spec.scope}.{spec.yaml_name}",
        "definition_ref": _field_semantics_id(spec),
        "field_path": field_path,
        "applicability": {
            "profiles": [profile.value],
            "documents": ["definition"],
            "node_kinds": [node_type],
        },
        "order": _field_order(spec),
        "status": editor_status,
    }
    return descriptor


def _node_example(node_type: str) -> dict[str, object]:
    payloads: dict[str, object] = {
        "command": "/review",
        "prompt": "Summarize the upstream result.",
        "bash": "printf 'ok\\n'",
        "script": "print('ok')",
        "loop": {
            "prompt": "Try again.",
            "until": "done",
            "max_iterations": 3,
        },
        "approval": {"message": "Continue?"},
        "cancel": "Cancellation requested.",
    }
    example: dict[str, object] = {
        "id": f"{node_type}-node",
        node_type: payloads[node_type],
    }
    if node_type == "script":
        example["runtime"] = "uv"
    return example


def node_kind_descriptors(
    profile: WorkflowLanguageProfile,
) -> list[dict[str, object]]:
    """Project the authoritative inventory into editor node-kind descriptors."""
    selected = _profile(profile)
    descriptors: list[dict[str, object]] = []
    for kind_order, node_type in enumerate(NODE_TYPES, start=1):
        payload = next(spec for spec in _specs("node") if spec.yaml_name == node_type)
        fields = [
            _field_descriptor(
                spec,
                selected,
                node_type,
                f"nodes[].{spec.yaml_name}",
            )
            for spec in _specs("node")
            if node_type in spec.applicable_node_types
            and _node_field_is_structural(spec, node_type, selected)
        ]
        fields.extend(
            _field_descriptor(
                spec,
                selected,
                node_type,
                field_path,
                parent_spec=next(
                    parent
                    for parent in _specs("node")
                    if parent.yaml_name
                    == {
                        "retry": "retry",
                        "loop": "loop",
                        "approval": "approval",
                        "approval_reject": "approval",
                        "agent": "agents",
                        "hook_event": "hooks",
                        "hook_entry": "hooks",
                        "hook_response": "hooks",
                        "hook_specific": "hooks",
                    }[spec.scope]
                ),
            )
            for spec, field_path in _nested_specs_for_kind(node_type, selected)
        )
        fields.sort(key=lambda item: (item["order"], item["field_path"]))
        descriptors.append({
            "id": node_type,
            "label": _humanize(node_type),
            "description": f"Author a Hermes {node_type} workflow node.",
            "field_path": f"nodes[].{node_type}",
            "applicability": {
                "profiles": [selected.value],
                "documents": ["definition"],
                "node_kinds": [node_type],
            },
            "widget": payload.widget,
            "section": "General",
            "order": kind_order,
            "status": _editor_status(_field_status(payload, selected).status),
            "examples": [_node_example(node_type)],
            "fields": fields,
        })
    return descriptors


def semantic_rule_descriptors(
    profile: WorkflowLanguageProfile,
) -> list[dict[str, object]]:
    """Publish only semantic rules enforced by the current workflow loader."""
    selected = _profile(profile)
    archon_v3 = selected is WorkflowLanguageProfile.ARCHON_2026_07
    definition_applicability = {
        "profiles": [selected.value],
        "documents": ["definition"],
    }
    return [
        {
            "id": "dag-topology",
            "label": "DAG topology",
            "description": (
                "Node identifiers and dependencies form one directed acyclic graph."
            ),
            "field_paths": ["nodes[].id", "nodes[].depends_on"],
            "applicability": definition_applicability,
            "status": "supported",
            "parameters": {
                "nodes_path": "nodes",
                "id_field": "id",
                "dependencies_field": "depends_on",
                "acyclic": True,
                "unique_ids": True,
            },
            "examples": [{"id": "build", "depends_on": ["prepare"]}],
        },
        {
            "id": "condition-expression",
            "label": "Condition expression",
            "description": (
                "Conditions are comparisons joined by optional AND or OR operators."
            ),
            "field_paths": ["nodes[].when"],
            "applicability": definition_applicability,
            "status": "supported",
            "parameters": {
                "expression_pattern": (
                    ARCHON_V3_WHEN_EXPRESSION_PATTERN
                    if archon_v3
                    else ECMASCRIPT_WHEN_EXPRESSION_PATTERN
                ),
                "expression_flags": "u",
                **(
                    {
                        "limits": {
                            "max_utf8_bytes": ARCHON_V3_CONDITION_MAX_BYTES,
                            "max_tokens": ARCHON_V3_CONDITION_MAX_TOKENS,
                            "max_parser_call_depth": ARCHON_V3_CONDITION_MAX_NESTING,
                        },
                        "comparison_operators": list(
                            ARCHON_V3_CONDITION_COMPARISON_OPERATORS
                        ),
                        "logical_operators": list(
                            ARCHON_V3_CONDITION_LOGICAL_OPERATORS
                        ),
                        "precedence": [
                            {
                                "operators": list(operators),
                                "associativity": associativity,
                                "higher_than": list(higher_than),
                            }
                            for operators, associativity, higher_than in (
                                ARCHON_V3_CONDITION_PRECEDENCE
                            )
                        ],
                        "evaluation": {
                            "order": ARCHON_V3_CONDITION_EVALUATION_ORDER,
                            "short_circuit": ARCHON_V3_CONDITION_SHORT_CIRCUIT,
                        },
                        "typed_operand_modes": {
                            key: list(value) if isinstance(value, tuple) else value
                            for key, value in (
                                ARCHON_V3_CONDITION_TYPED_OPERAND_MODES.items()
                            )
                        },
                    }
                    if archon_v3
                    else {}
                ),
            },
            "examples": [
                "$prepare.output.status == 'ready' && $inspect.output.count >= 2",
                *([] if archon_v3 else ["$café.output.status == 'ready'"]),
            ],
        },
        {
            "id": "condition-output-reference",
            "label": "Condition output reference",
            "description": (
                "Conditions may reference only existing upstream node outputs."
            ),
            "field_paths": ["nodes[].when"],
            "applicability": definition_applicability,
            "status": "supported",
            "parameters": {
                "syntax": "$ID.output(.path)*",
                "pattern": (
                    ECMASCRIPT_ARCHON_V3_OUTPUT_REFERENCE_PATTERN
                    if archon_v3
                    else ECMASCRIPT_WHEN_REFERENCE_PATTERN
                ),
                "pattern_flags": "u",
                "node_id_capture_group": 1,
                "require_upstream": True,
                **({"require_direct_dependency": True} if archon_v3 else {}),
            },
            "examples": [
                "$prepare.output.status == 'ready'",
                *([] if archon_v3 else ["$café.output.status == 'ready'"]),
            ],
        },
        *(
            [
                {
                    "id": "strict-output-reference",
                    "label": "Strict output reference",
                    "description": (
                        "Every output reference names a direct dependency and uses "
                        "the closed ASCII node and path grammar."
                    ),
                    "field_paths": [
                        "nodes[].when",
                        "nodes[].prompt",
                        "nodes[].bash",
                        "nodes[].script",
                        "nodes[].command",
                        "nodes[].loop.prompt",
                        "nodes[].loop.until_bash",
                        "nodes[].approval.message",
                        "nodes[].approval.on_reject.prompt",
                    ],
                    "applicability": definition_applicability,
                    "status": "supported",
                    "parameters": {
                        "syntax": "$ID.output(.path)*",
                        "pattern": ECMASCRIPT_ARCHON_V3_OUTPUT_REFERENCE_PATTERN,
                        "pattern_flags": "u",
                        "node_id_capture_group": 1,
                        "require_upstream": True,
                        "require_direct_dependency": True,
                    },
                    "examples": ["$prepare.output.status"],
                }
            ]
            if archon_v3
            else []
        ),
        {
            "id": "companion-node-reference-list",
            "label": "Companion node references",
            "description": (
                "Companion outward-action declarations reference existing node IDs."
            ),
            "field_paths": ["sidecar.outward_action_nodes"],
            "applicability": {
                "profiles": [selected.value],
                "documents": ["sidecar"],
            },
            "status": "supported",
            "parameters": {
                "reference_kind": "node_id_list",
                "pattern": r"^(.+)$",
                "node_id_capture_group": 1,
                "require_upstream": False,
            },
            "examples": [["publish"]],
        },
    ]


def contract_documentation(
    profile: WorkflowLanguageProfile,
) -> dict[str, object]:
    selected = _profile(profile)
    applicability = {
        "profiles": [selected.value],
        "documents": ["definition"],
    }
    profile_sidecar = json.dumps(
        {"language_compatibility": selected.value},
        separators=(",", ":"),
    )
    definition = json.dumps(
        {
            "name": "example",
            "description": "Minimal offline authoring example",
            "nodes": [{"id": "start", "bash": "printf 'ok\\n'"}],
        },
        separators=(",", ":"),
    )
    phase3_topics = (
        [
            {
                "id": "persistent-session-recovery",
                "operator_surfaces": [
                    "workflow doctor",
                    "Run Inspector recovery evidence",
                ],
            },
            {
                "id": "extension-options",
                "parameters": {
                    "_".join(
                        name for name, _json_type, _shape in _AI_EXTENSION_NODE_OPTIONS
                    ): "options_not_node_kinds",
                    "loops_includes_phase": ARCHON_EXTENSION_EXPANSION_PHASE,
                },
            },
        ]
        if selected is WorkflowLanguageProfile.ARCHON_2026_07
        else []
    )
    return {
        "topics": [
            {
                "id": "workflow-definition",
                "title": "Workflow definition",
                "description": "Definition fields and node authoring structure.",
                "body": (
                    "The definition YAML is the workflow graph authority. Each node "
                    "declares exactly one node-kind field."
                ),
                "field_paths": [_contract_path(spec) for spec in _specs("definition")],
                "applicability": applicability,
                "examples": [definition],
            },
            {
                "id": "dag-and-conditions",
                "title": "DAG and conditions",
                "description": "Dependency and condition-reference rules.",
                "body": (
                    "Dependencies must exist and remain acyclic. Conditions may "
                    "reference only upstream node outputs."
                ),
                "field_paths": [
                    "nodes[].id",
                    "nodes[].depends_on",
                    "nodes[].when",
                ],
                "applicability": applicability,
                "examples": ["$start.output.status == 'ready'"],
            },
            {
                "id": "companion-policy",
                "title": "Companion policy",
                "description": "Optional metadata and policy companion fields.",
                "body": (
                    "The optional companion YAML may declare metadata and policy but "
                    "never graph topology or trust authority."
                ),
                "field_paths": [_contract_path(spec) for spec in _specs("sidecar")],
                "applicability": {
                    "profiles": [selected.value],
                    "documents": ["sidecar"],
                },
                "examples": [profile_sidecar],
            },
            {
                "id": "stable-codes",
                "code_source": "compatibility_codes",
            },
            *phase3_topics,
        ],
        "examples": [
            {
                "id": "minimal-workflow",
                "title": "Minimal workflow",
                "description": "A one-node workflow with its optional companion.",
                "definition": definition,
                "sidecar": profile_sidecar,
            }
        ],
    }


def _contract_digest(envelope: dict[str, object]) -> str:
    canonical = canonical_contract_json(envelope).encode()
    return f"sha256:{sha256(canonical).hexdigest()}"


def _canonical_float(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("canonical contract JSON requires finite numbers")
    if value == 0:
        return "0"

    text = repr(value).lower()
    if "e" not in text:
        return text.removesuffix(".0")

    mantissa, raw_exponent = text.split("e", 1)
    exponent = int(raw_exponent)
    sign = ""
    if mantissa.startswith("-"):
        sign = "-"
        mantissa = mantissa[1:]
    whole, separator, fraction = mantissa.partition(".")
    digits = whole + fraction
    decimal_position = len(whole) + exponent
    magnitude = abs(value)

    if 1e-6 <= magnitude < 1e21:
        if decimal_position <= 0:
            return f"{sign}0.{('0' * -decimal_position)}{digits}"
        if decimal_position >= len(digits):
            return f"{sign}{digits}{'0' * (decimal_position - len(digits))}"
        return f"{sign}{digits[:decimal_position]}.{digits[decimal_position:]}"

    normalized_mantissa = whole
    if separator and fraction.rstrip("0"):
        normalized_mantissa += f".{fraction.rstrip('0')}"
    exponent_text = f"+{exponent}" if exponent >= 0 else str(exponent)
    return f"{sign}{normalized_mantissa}e{exponent_text}"


def canonical_contract_json(value: object) -> str:
    """Serialize JSON with recursive key sorting and JavaScript number spelling."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, int):
        if abs(value) > 9_007_199_254_740_991:
            raise ValueError("canonical contract JSON requires safe integers")
        return str(value)
    if isinstance(value, float):
        return _canonical_float(value)
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical contract JSON object keys must be strings")
        return (
            "{"
            + ",".join(
                f"{json.dumps(key, ensure_ascii=False)}:{canonical_contract_json(value[key])}"
                for key in sorted(
                    value,
                    key=lambda item: item.encode("utf-16-be", "surrogatepass"),
                )
            )
            + "}"
        )
    if isinstance(value, list | tuple):
        return "[" + ",".join(canonical_contract_json(item) for item in value) + "]"
    raise TypeError(
        f"canonical contract JSON does not support {type(value).__name__} values"
    )


def workflow_authoring_contract(
    profile: WorkflowLanguageProfile,
) -> dict[str, object]:
    """Return one bounded, side-effect-free workflow authoring contract."""
    selected = _profile(profile)
    node_kinds = node_kind_descriptors(selected)
    referenced_definitions = frozenset(
        str(field["definition_ref"])
        for node_kind in node_kinds
        for field in node_kind["fields"]
    )
    envelope: dict[str, object] = {
        "schema_version": 1,
        "contract_reader_version": CONTRACT_READER_VERSION,
        "editor_projection_version": EDITOR_PROJECTION_VERSION,
        "profile": selected.value,
        "normalizer_version": CURRENT_NORMALIZER_BY_PROFILE[selected],
        "field_definitions": field_definition_catalog(
            selected, definition_ids=referenced_definitions
        ),
        "definition_schema": definition_json_schema(selected),
        "sidecar_schema": sidecar_json_schema(selected),
        "node_kinds": node_kinds,
        "semantic_rules": semantic_rule_descriptors(selected),
        "compatibility_codes": compatibility_code_catalog(selected),
        "documentation": contract_documentation(selected),
        "limits": {
            "max_document_bytes": MAX_WORKFLOW_DOCUMENT_BYTES,
            "max_contract_bytes": CONTRACT_MAX_BYTES,
            "reserved_growth_bytes": CONTRACT_RESERVED_GROWTH_BYTES,
            "section_max_bytes": dict(CONTRACT_SECTION_MAX_BYTES),
        },
        "x-hermes-provenance": {
            "producer": "hermes-agent",
            "command": "hermes workflow schema",
            "field_authority": "plugins.workflow.language_schema.FIELD_INVENTORY",
        },
        "x-hermes-filenames": {
            "definition_extensions": [".yaml", ".yml"],
            "reserved_definition_suffix": ".hermes.yaml",
            "companion_suffix": ".hermes.yaml",
        },
        "x-hermes-pairing": {
            "definition": "<name>.yaml or <name>.yml",
            "companion": "<definition-stem>.hermes.yaml",
            "companion_optional": True,
        },
    }
    return {**envelope, "contract_digest": _contract_digest(envelope)}
