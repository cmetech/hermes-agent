# Phase 3 Task 5 Independent Specification Rereview 1

**Reviewed implementation:** `86158b7704f7f573bc82fc2fa50c04238756308e`

**Reviewed fix:** `02fcd3248bd7818ab3130ba4a71fecaf7c705055`

**Final reviewed tree:** `6d842cb861abb742b25a25794d40972b69050fc9`

**Task baseline:** `f750be100c003e15a8ada597c340be0f9f73daab`

**Verdict:** APPROVED

**Findings:** 0 Critical, 0 Important, 0 Minor

## Scope and evidence

I reread the original Task 5 specification and quality reviews, inspected the
complete review-to-fix diff, and traced the final condition parser/evaluator,
strict output resolver, scheduler graph resolution, output cache, store
journal/rebuild, durable catalog, and authoring-contract paths. The requested
HEAD and final tree match. The worktree was clean before this retained report.

The fix is confined to Task 5 behavior and tests. It changes typed condition
comparison, backend-authored condition metadata, lazy scheduler condition
resolution, and the existing Task 5 store transition's diagnostic boundary.
It does not add durable resolution waits, backoff fields/codes, Bash rendering,
session recovery, loops/includes, or any other Task 6+ production behavior.
The `store.py` change remains the required minimal persistence seam for
claim-free condition outcomes.

## Closure of original findings

### Spec I-1 — CLOSED: equality no longer coerces schemaless numeric text

`_evaluate_clause()` now allows `schemaless_text` only when the operator is in
the centrally defined ordered set. Unquoted `==` and `!=` therefore require a
canonical finite numeric LHS and reject schemaless decimal text with
`condition_operand_type`. RED-derived cases cover whitespace-preserving and
decimal-equivalent spellings for both equality operators.

Ordered comparisons still accept schemaless whole-output exact decimal text
after outer ASCII whitespace removal, and quoted decimal ordering remains
supported. Quoted equality remains exact string equality, while declared
structured numeric-looking strings remain non-coercible. The finite `Decimal`
path and boolean/null/container failures are unchanged.

### Spec I-2 — CLOSED: the v3 authoring projection publishes central bounded semantics

The central language inventory now owns the equality, ordered, comparison, and
logical operator sets; precedence and associativity; evaluation order and
short-circuit flag; and typed operand modes. The runtime parser consumes the
central operator sets, while the Archon v3 `condition-expression` projection
derives its machine-readable limits and semantic fields from the same central
constants.

The projection includes the 16,384 UTF-8 byte limit, 256-token limit,
three-call parser-depth limit, comparison/logical operators, `&&`-over-`||`
left associativity, left-to-right short circuiting, and the exact string and
numeric operand modes. Relationship tests bind the projection to the central
constants and keep the complete authoring contract below its established
bound. The additional fields are gated to Archon v3; the legacy condition
projection remains its prior expression pattern and flags.

### Quality I-1 — CLOSED: scheduler condition resolution is lazy and clause-driven

For Archon v3, `_resolve_graph()` no longer materializes every output before
condition evaluation. It passes a node-scoped resolver callback to the v3
evaluator, and `_output_values(..., node_ids=...)` verifies and caches only the
node referenced by a reached clause. The evaluator's existing left-to-right
loops therefore short-circuit before skipped `||` and `&&` operands cause
publication reads or cache entries.

Real scheduler tests create two successful direct dependencies, instrument the
actual strict `resolve_node_output()` boundary, and prove both true-`||` and
false-`&&` paths resolve/cache only the reached left producer. The legacy path
still calls its prior eager `resolve_legacy_output_values()` adapter once and
retains existing comparison/reparse behavior.

### Quality I-2 — CLOSED: durable diagnostics enforce valid UTF-8 byte bounds

The Task 5 store transition now validates condition codes and messages as
UTF-8, rejects lone surrogates before mutation/journal serialization, redacts
the message, and truncates the encoded result to the central 2,000-byte limit
without retaining a partial code point. The same bounded value is written to
both `last_error` and the `node_failed` event payload.

Direct store tests cover exact and overflowing ASCII and multibyte boundaries,
a split-code-point boundary, invalid Unicode with no state/event mutation, and
full journal reconstruction after deleting `run.json`. Rebuilt diagnostics
remain valid UTF-8 and within 2,000 bytes, with zero attempts and zero retry
consumption.

## Task 5 contract verification

The final implementation retains the exact bounded grammar, comparison and
logical operators, ASCII whitespace, rejected general-expression forms,
`&&` precedence, left-to-right short circuiting, strict reference grammar and
typed resolver reuse, finite decimal ordering, schemaless-versus-structured
string distinction, stable bounded condition/reference errors, false-to-skip
and error-to-failed pre-claim CAS transitions, no executor/provider attempt or
retry charge, no `on_error` override, behavior-linked durable codes, and exact
legacy dispatch.

The complete Task 5 and adjacent regression gate was run only through the
repository wrapper with flaky file retries disabled:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_conditions.py \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_scheduler.py \
  tests/plugins/workflow/test_parallel_scheduler.py \
  tests/plugins/workflow/test_compat_matrix.py \
  tests/plugins/workflow/test_schema.py \
  tests/plugins/workflow/test_language_schema.py \
  tests/plugins/workflow/test_strict_output_references.py \
  tests/plugins/workflow/test_typed_publication.py \
  tests/plugins/workflow/test_typed_publication_recovery.py \
  tests/plugins/workflow/test_store.py \
  tests/plugins/workflow/test_crash_recovery.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_performance_bounds.py

Result: 14 files, 1,048 tests passed, 0 failed, no retries.
```

The fix commit range `e9996aae3..02fcd3248` passes `git diff --check`. The
broader review range includes pre-existing trailing spaces in the retained
quality-review Markdown metadata only; no production or test file in the fix
has whitespace errors.

## Conclusion

All two specification and two quality findings are closed. Task 5 now meets
the approved typed-condition, strict-reference, scheduler-transition,
persistence, catalog, authoring-projection, legacy-compatibility, and scope
requirements. No further specification changes are required before the
controller closes Task 5.
