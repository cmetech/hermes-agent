# Phase 3 Task 5 Independent Specification Review

**Reviewed commit:** `86158b7704f7f573bc82fc2fa50c04238756308e`

**Reviewed tree:** `765ed106bfb8cf6cb51b707261678996afa60c95`

**Baseline:** `f750be100c003e15a8ada597c340be0f9f73daab`

**Verdict:** CHANGES REQUIRED

**Findings:** 0 Critical, 2 Important, 0 Minor

## Scope and evidence

I read the complete root `AGENTS.md`, the complete approved Phase 3 design,
the complete Task 5 plan, the complete baseline-to-target production/test
diff, and the relevant strict-reference, scheduler, store, durable-code, and
authoring-contract paths. The requested commit and tree identities match, and
the worktree was clean before this report.

The production diff is bounded to the Task 5 parser/evaluator, v3 schema and
catalog wiring, scheduler dispatch, and one store transition needed to make a
false/error outcome durable before claim. The `store.py` deviation from the
task's file list is required and minimal: it adds the design-mandated
pending-to-skipped/failed CAS under the existing run lock and does not add
execution or Task 6 resolution-wait behavior. No Task 6+ production surface
is introduced.

The implementation otherwise provides the closed comparison/operator grammar,
ASCII whitespace handling, `&&`-before-`||` precedence, left-to-right short
circuiting, bounded parser inputs/tokens/call depth, strict reference lexer and
resolver reuse, finite `Decimal` ordering, structured-string preservation,
stable condition exceptions, claim-free false/error transitions, durable
catalog emitters, v3-only scheduler dispatch, and the unchanged legacy adapter.

Focused verification was run only through the repository wrapper with flaky
file retries disabled:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_conditions.py \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_scheduler.py \
  tests/plugins/workflow/test_parallel_scheduler.py \
  tests/plugins/workflow/test_compat_matrix.py

Result: 5 files, 165 tests passed, 0 failed, no retries.
```

Adjacent authoring/reference verification was also run:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
scripts/run_tests.sh \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_strict_output_references.py

Result: 2 files, 710 tests passed, 0 failed, no retries.
```

The implementation range also passes `git diff --check`.

## Important findings

### I-1 — Numeric equality still coerces schemaless text into a number

The approved typed-equality contract is explicit: a quoted RHS compares only
with a string LHS, while an unquoted decimal RHS compares only with a finite
numeric LHS. Schemaless whole-output decimal text is a compatibility allowance
for **ordered comparison** after outer ASCII whitespace is removed; it must not
reintroduce Phase 2 string/number equality coercion.

`_evaluate_clause()` sends every non-quoted-equality operation, including
unquoted `==` and `!=`, through `_numeric_left()`
(`plugins/workflow/conditions.py:290-297`). `_numeric_left()` parses a string
whenever the reference is a schemaless whole output
(`plugins/workflow/conditions.py:244-255`). Consequently schemaless text such
as `" 2.50\t"` compares equal to the numeric literal `2.5`, even though the
canonical LHS type is string. The new test suite explicitly locks this
non-compliant result in as expected behavior
(`tests/plugins/workflow/test_phase3_conditions.py:163-193`, especially line
173).

This violates type-directed equality and the Phase 3 prohibition on falling
back to truthiness/coercion adapters. It also makes equality and ordering share
an undocumented conversion rule despite the design distinguishing them.

**Required remediation:** Add RED cases proving schemaless numeric text with an
unquoted RHS fails `condition_operand_type` for `==` and `!=`, including outer
ASCII whitespace and decimal-equivalent spellings. Restrict schemaless decimal
parsing to `<`, `<=`, `>`, and `>=`. Preserve quoted exact-string equality and
ordered quoted-number support, and keep structured numeric-looking strings
non-coercible.

### I-2 — The backend authoring contract omits the bounds and evaluation semantics the runtime enforces

Task 5 centralizes runtime limits in `language_schema.py` as 16,384 UTF-8
bytes, 256 tokens, and parser call depth 3
(`plugins/workflow/language_schema.py:25-28`), but the backend-authored
`condition-expression` projection still exposes only a regex and Unicode flag
(`plugins/workflow/language_schema.py:2113-2128`). The `when` field inventory
also has no corresponding authored bound (`plugins/workflow/language_schema.py:981`),
so its generated field/schema descriptor supplies neither a byte limit nor a
token limit.

The projection does not state the operator set, that `&&` binds tighter than
`||`, that evaluation short-circuits left-to-right, or the typed equality and
numeric-ordering rules. A Desktop/editor or installed workflow-builder
consumer therefore cannot derive the bounded Task 5 contract from backend
truth: it can accept an expression matching the regex that the loader later
rejects solely for byte/token capacity, and it has no authoritative metadata
for the condition semantics. Existing schema tests assert only that an
expression pattern exists, so the runtime/projection mismatch stays green
(`tests/plugins/workflow/test_language_schema.py:183-210`).

This conflicts with the Phase 3 boundary that authoring projections remain
bounded and backend-authored, and leaves `language_schema.py` as an incomplete
central authority for the semantics implemented in this task.

**Required remediation:** Extend the Archon v3 `condition-expression`
descriptor (and the field/editor projection where appropriate) with bounded,
machine-readable parameters for UTF-8 byte limit, token limit, supported
operators, precedence/associativity, short-circuit behavior, and the typed
operand modes. Add relationship tests showing those projected values derive
from the same central constants/rules the runtime consumes. Do not alter the
legacy projection or add a renderer-side parser.

## Review conclusion

The dedicated bounded parser, strict reference reuse, finite decimal ordering,
pre-claim store CAS, failure/skip durability, retry isolation, v3 gate, legacy
adapter, and behavior-linked error catalog are otherwise aligned. Task 5 is
not specification-complete because numeric equality still performs a forbidden
schemaless string-to-number coercion and the backend authoring contract omits
the runtime's enforced capacity and evaluation semantics. Both Important
findings should be fixed and independently reverified before Task 5 closes.
