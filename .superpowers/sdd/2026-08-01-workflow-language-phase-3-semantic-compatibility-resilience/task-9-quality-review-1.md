# Phase 3 Task 9 Quality Review 1

**Reviewed candidate:** `dbfce787a6bc3a0066968ff70c3c7a427ddd0387`

**Reviewed tree:** `cb27371bed0138c5ad1412a5ce007adfb35092e7`

**Verdict:** FAIL

**Findings:** 0 Critical, 2 Important, 0 Minor

## Important findings

### I1. Conservative provider counts are persisted as falsely exact

`RetryLedgerGrant.charge()` treats every in-range integer as exact. The scheduler passes it `provider_attempts` without carrying the executor's exactness provenance, then merges the resulting `provider_attempts_exact: true` into durable attempt metadata.

That is incorrect for several real AI paths. `AgentNodeExecutor` deliberately converts missing or invalid evidence into the conservative integer `grant - 1` for runner exceptions and generic failures. Structured repair likewise records `audit.provider_attempts_exact: false` while exposing the conservative integer at top level. At the scheduler boundary those conservative integers satisfy the range check and become `provider_attempts_exact: true`.

The numeric charge remains fail-safe, but the durable evidence contradicts its source and can later be projected as backend truth. The new missing/invalid-evidence scheduler test uses a fake executor that omits the integer entirely, so it covers the scheduler fallback but not the real `AgentNodeExecutor -> scheduler -> store` path that exhibits the contradiction.

Evidence:

- `plugins/workflow/models.py:348-358` infers exactness from integer shape alone.
- `plugins/workflow/scheduler.py:3337-3352` supplies the integer and overwrites metadata with ledger evidence.
- `plugins/workflow/executors/ai.py:428-448` marks conservative repair accounting false only in nested audit while returning `grant - 1` at top level.
- `plugins/workflow/executors/ai.py:1011-1037` and `1169-1180` produce the same conservative integer for missing/invalid runner evidence.
- `tests/plugins/workflow/test_retry.py:241-305` bypasses those real executor paths.

Required correction: propagate an explicit exact/unknown evidence identity through the executor boundary, or keep conservative counts distinguishable when charging. Add end-to-end tests for missing, invalid, repair, and fallback evidence that assert both full-grant charging and `provider_attempts_exact: false` in the stored attempt.

### I2. Declaring a node outward overrides known fatal failure classes

`classify_failure()` checks `outward_action` before the closed fatal set. Consequently authentication, authorization, credit exhaustion, validation, resource-limit, contract-drift, and even explicitly known-no-effect fatal results classify as `UNKNOWN_OUTCOME` whenever the node appears in `outward_action_nodes`.

`_persist_result()` passes the node-level outward flag for every v3 result, so these known fatal outcomes become paused reconciliation rather than terminal failures. This violates the Phase 3 contract that fatal failures are fatal regardless of `on_error`; outward uncertainty should prevent replay of otherwise retryable/unknown outcomes, not erase a stronger known-fatal classification. It also needlessly holds the run/lane for operator reconciliation on errors such as authentication that require no effect reconciliation.

Evidence:

- `plugins/workflow/scheduler.py:303-315` returns `UNKNOWN_OUTCOME` at line 306 before consulting `_FATAL_FAILURES`.
- `plugins/workflow/scheduler.py:3483-3491` applies the outward flag unconditionally at the real persistence boundary.
- The direct diagnostic at the reviewed tree classified `authentication`, `authorization`, `credit_exhausted`, `validation`, `cleanup_failed`, `resource_limit`, and `workflow_execution_semantics_mismatch` as `unknown_outcome` with `outward_action=True` and `known_no_effect=True`.
- `tests/plugins/workflow/test_retry.py:308-331` covers outward transient timeout and non-outward fatal rows, but no outward/fatal cross-product or end-to-end outward fatal transition.

Required correction: preserve cancellation first and fatal classification before applying outward uncertainty to eligible non-fatal outcomes. Add direct and store-transition tests showing outward fatal failures terminate without retry or reconciliation while outward transient/unknown outcomes still pause safely.

## Verified strengths

- The sealed grant is derived from durable `retry_consumed` and bounded by effective total attempts.
- The charge equation counts the workflow attempt once and validated additional provider attempts once.
- Missing/invalid counts consume the full remaining grant numerically.
- Structured-output total provider calls are converted to additional calls once inside the AI executor.
- Both `advance()` and `advance_all()` converge through `_execute_claim()` and `_persist_result()`.
- Retry wake uses the existing locked CAS, with a new multiprocess one-winner test.
- Cancellation before executor/provider launch avoids a durable charge.
- Legacy execution continues through the pre-existing policy/accounting branch.
- No Task 10 production surface was introduced.

## Verification evidence

Command:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_execution_semantics.py \
  tests/plugins/workflow/test_retry.py \
  tests/plugins/workflow/test_provider_failures.py \
  tests/plugins/workflow/test_ai_executor.py \
  tests/plugins/workflow/test_parallel_scheduler.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_shutdown_recovery.py
```

Result: **7 files, 230 tests passed, 0 failed, no flaky retries**.

Static checks:

```text
../../.venv/bin/ruff check <all seven Task 9 changed files>
git diff --check dbfce787^ dbfce787
```

Result: **PASS**.

The worktree was clean before this retained review report was written. No production or test files were modified by this review.
