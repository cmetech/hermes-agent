# Phase 3 Task 5 Independent Quality Review 1

**Review date:** 2026-08-02  
**Baseline:** `f750be100`  
**Implementation:** `86158b7704f7f573bc82fc2fa50c04238756308e`  
**Implementation tree:** `765ed106bfb8cf6cb51b707261678996afa60c95`  
**Verdict:** CHANGES REQUIRED

## Severity summary

- Critical: 0
- Important: 2
- Minor: 0

## Scope and evidence reviewed

I read the complete repository `AGENTS.md`, the complete approved Phase 3
design, the complete implementation plan and Task 5 requirements, the exact
`f750be100..86158b770` diff, the strict output resolver and scheduler callers,
the run-store journal/rebuild and CAS paths, the durable-code projection, and
all new and adjacent tests. The review was read-only apart from this retained
report.

The following commands were run with flaky file retries disabled:

1. Exact Task 5 gate:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_phase3_conditions.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_compat_matrix.py`
   — 5 files, 165 tests passed, 0 failed.
2. Adjacent schema, resolver, store, recovery, concurrency, and bound gate:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_schema.py tests/plugins/workflow/test_language_schema.py tests/plugins/workflow/test_strict_output_references.py tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_coordinator_multiprocess.py tests/plugins/workflow/test_performance_bounds.py`
   — 9 files, 873 tests passed, 0 failed.
3. Ruff on every changed Python file — clean.
4. `git diff --check f750be100..86158b7704f7f573bc82fc2fa50c04238756308e`
   — clean.

## Important findings

### I-1 — The real scheduler eagerly resolves skipped branches before condition short-circuiting

`RunScheduler._resolve_graph()` builds `outputs` before it parses or evaluates
any condition (`plugins/workflow/scheduler.py:1118-1120`). `_output_values()`
then walks every successful node in the run (`plugins/workflow/scheduler.py:683-876`),
opens and verifies its publication, and may populate the shared cache. Only
after that eager pass does `evaluate_v3_condition()` short-circuit clauses
(`plugins/workflow/scheduler.py:1146-1156`).

The pure evaluator test at
`tests/plugins/workflow/test_phase3_conditions.py:75-92` supplies a prebuilt
dictionary with omitted keys. It proves the boolean loop does not call
`Mapping.get()` for the skipped clauses, but it does not exercise the real
scheduler/resolver boundary. In a real run, an expression such as
`$left.output == 1 || $right.output == 1` still reads and verifies `right` even
when `left` is true. This violates the approved requirement that evaluation be
short-circuiting without resolving skipped references. It also creates the
wrong seam for Task 6: a skipped branch can already incur host I/O and cache
effects before the evaluator decides it is unreachable.

**Required remediation:** Make v3 condition operand resolution lazy and
clause-driven (for example, pass a bounded resolver/accessor into the evaluator
instead of an eagerly materialized all-output dictionary). Preserve the legacy
eager adapter. Add a real scheduler test with a successful first clause and a
second direct-dependency output whose resolver would fail or record a read;
prove the second reference is not opened, resolved, cached, or surfaced. Also
cover the corresponding `&&` short-circuit case.

### I-2 — The durable condition transition does not enforce its byte diagnostic bound

Task 5 defines `ARCHON_V3_CONDITION_DIAGNOSTIC_MAX_BYTES = 2_000`, and
`WorkflowConditionError` enforces that bound on its fixed ASCII message.
However, the actual persistence authority accepts any non-empty `message` and
passes it through `_sanitize_diagnostic()`
(`plugins/workflow/store.py:10224-10234`). That shared sanitizer truncates to
2,000 Python characters, not UTF-8 bytes (`plugins/workflow/store.py:419-422`).
A 2,000-character multibyte message can therefore persist roughly 8,000 UTF-8
bytes in `last_error` and twice in the full journal frame/payload. A lone
surrogate can also reach JSON serialization instead of being rejected at this
boundary.

Current scheduler-produced condition/reference messages are short ASCII, so
the focused suite stays green. The direct store test at
`tests/plugins/workflow/test_phase3_conditions.py:279-326` uses only a short
ASCII message and does not prove the durable byte contract. The store method is
the crash-consistent authority and must defend the bound independently rather
than relying on every caller to remain well behaved.

**Required remediation:** Validate and/or truncate sanitized diagnostics by
UTF-8 bytes without splitting a code point, reject invalid Unicode, and use the
central Task 5 byte constant at the persistence boundary. Add direct store and
journal-rebuild tests at the exact ASCII and multibyte byte boundaries and a
rejection test for invalid Unicode; assert both `last_error` and event payload
remain within the declared bound.

## Positive findings

- The parser is deliberately small and bounded; it uses no general expression
  evaluation, applies ASCII whitespace and reference grammar consistently, and
  rejects trailing/general-expression syntax.
- `&&` precedence over `||` and left-to-right clause evaluation are correctly
  represented in the AST and pure evaluator.
- Equality and ordering are type-directed. Booleans are not integers, declared
  structured strings are not coerced, schemaless whole text is the only LHS
  string eligible for decimal parsing, and finite `Decimal` comparison avoids
  binary-float ordering surprises.
- Stable condition and strict-reference failures become per-node journaled
  `failed` transitions with zero attempts and zero retry consumption. Valid
  false conditions become `condition_false` skips, and `on_error: all` does not
  route either outcome into the executor/provider retry path.
- The run lock plus pending-state CAS makes repeated/concurrent transition
  calls idempotent, and the full-projection journal frame rebuilds the
  zero-attempt failure correctly.
- New runtime codes are registered in the additive durable catalog with bounded
  meanings and real emitter coverage. The diff adds no Phase 4/5 behavior,
  model tool, provider response, or raw output/evidence surface.
- Exact legacy evaluator behavior and adjacent legacy scheduler suites remain
  green.

## Final assessment

Task 5 is not ready to hand off. Its typed comparison and durable zero-attempt
transition core is sound, and 1,038 focused/adjacent tests pass without retry,
but the real scheduler does not honor the promised lazy short-circuit boundary
and the persistence layer does not enforce the declared diagnostic byte cap.
Both Important findings need a bounded fix round, RED coverage, and fresh
focused verification.
