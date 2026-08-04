# Phase 3 Task 5 Independent Quality Rereview 1

**Review date:** 2026-08-02

**Original implementation:** `86158b7704f7f573bc82fc2fa50c04238756308e`

**Reviewed closure HEAD:** `02fcd3248bd7818ab3130ba4a71fecaf7c705055`

**Reviewed closure tree:** `6d842cb861abb742b25a25794d40972b69050fc9`

**Verdict:** PASS

## Severity summary

- Critical: 0
- Important: 0
- Minor: 0

## Scope and evidence reviewed

I reread both original Task 5 reports, inspected the complete
`86158b770..02fcd3248` production and test diff, and followed the affected
condition parser, language-contract projection, scheduler output resolver and
cache, legacy adapter, run-store CAS/journal/rebuild path, and all new closure
tests. This was a read-only review apart from this retained report.

The combined focused and adjacent closure gate ran only through the required
wrapper with flaky file retries disabled:

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

Ruff passed on all six changed production/test files. The scoped implementation
and test diff passes `git diff --check`.

## Closure of original quality findings

### I-1 — Closed: v3 condition resolution is now genuinely clause-lazy

`_resolve_graph()` no longer creates an all-output mapping for Archon v3. It
passes `resolve_condition_output` into `evaluate_v3_condition()`, and the
evaluator calls that accessor only when it reaches a clause. `||` returns after
a true group and `&&` stops after a false clause, so an unreachable reference
does not enter `_output_values()`, `resolve_node_output()`, or the scheduler's
resolved-output cache.

The new scheduler-level table test covers both directions with real completed
artifacts. It records resolver calls, makes a right-side read fail if attempted,
and asserts no right-side cache key exists. The `||` case resolves only the
true left operand and transitions the consumer to ready; the `&&` case resolves
only the false left operand and transitions it to `condition_false` skipped.

Successful repeated references remain protected by the existing lock- and
weight-bounded cache. Missing/integrity results are not introduced as negative
cache entries. The legacy branch still constructs the complete legacy output
mapping before calling the unchanged `evaluate_condition()` adapter, so this
fix does not alter unversioned, `hermes-legacy`, or admitted v1/v2 behavior.

The change does not add Task 6 resolution waits: transient-read classification
and durable backoff remain deferred exactly as planned.

### I-2 — Closed: durable diagnostics are bounded by valid UTF-8 bytes

The store now validates both code and message UTF-8 before mutation and uses a
dedicated v3 condition sanitizer. Redaction happens before sizing; values over
2,000 bytes are sliced as bytes and decoded with incomplete trailing code
points discarded. As a result, neither `last_error` nor the `node_failed`
payload can exceed `ARCHON_V3_CONDITION_DIAGNOSTIC_MAX_BYTES`, and a lone
surrogate fails before the pending-node CAS changes state.

The table tests cover exact and overflow ASCII, exact and overflow two-byte
Unicode, and a truncation exactly inside a multibyte code point. They verify
the live projection, event payload, journal deletion/rebuild, and the byte cap.
The invalid-Unicode test verifies rejection leaves the node pending and emits
no failure event. The transition continues to run under the existing per-run
lock and pending-state CAS, so concurrent attempts are idempotent; the adjacent
parallel, multiprocess, store, and crash-recovery suites remain green.

## Closure of original specification findings

- Schemaless numeric text is no longer accepted by unquoted `==` or `!=`.
  Decimal text conversion is restricted to ordered operators. Quoted equality
  remains exact string comparison, canonical finite numbers retain exact
  numeric equality, structured numeric-looking strings remain strings, and
  booleans/null/containers still fail through the typed error contract.
- The Archon v3 backend condition descriptor now publishes limits, operator
  sets, precedence and associativity, left-to-right short-circuit evaluation,
  and typed operand modes from the same constants consumed by the parser and
  evaluator. Legacy projection remains unchanged, and the bounded contract
  size test remains comfortably below its ceiling.

## Security, compatibility, and scope assessment

- Parser input, token count, parser depth, and diagnostics remain centrally
  bounded; no Python/general expression evaluation is introduced.
- Decimal comparison remains finite and exact, with no NaN/infinity, exponent,
  locale-number, hexadecimal, boolean-as-integer, or structured-string
  coercion path.
- False and typed/reference-error conditions remain pre-claim, zero-attempt,
  zero-provider, zero-retry-consumption transitions. `on_error` cannot replay
  them.
- Journal frames contain only bounded stable diagnostics and no raw output,
  provider response, path, or new evidence surface.
- The closure adds no Phase 4/5 semantics, Task 6 durable wait behavior, core
  tool, prompt mutation, or API/Desktop-side evaluator.

## Final assessment

All original Task 5 specification and quality findings are closed at the exact
reviewed HEAD and tree. The implementation is ready for the controller's Task
5 handoff.
