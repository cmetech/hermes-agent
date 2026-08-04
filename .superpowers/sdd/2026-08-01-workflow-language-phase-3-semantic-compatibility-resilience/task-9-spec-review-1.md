# Phase 3 Task 9 Specification Review 1

**Verdict:** CHANGES REQUIRED

**Reviewed HEAD:** `dbfce787a6bc3a0066968ff70c3c7a427ddd0387`

**Reviewed tree:** `cb27371bed0138c5ad1412a5ce007adfb35092e7`

**Task baseline:** `e89c5dce4`

**Severity counts:** 0 Critical, 4 Important, 0 Minor

## Scope reviewed

I read the complete retry-normalization/accounting portion of the approved
Phase 3 design, the complete Task 9 plan, and the full
`e89c5dce4..dbfce787a` production/test diff. I inspected normalization and
sealed evidence, both scheduler entrypoints, durable retry wake/claim paths,
the AI provider and structured-repair accounting paths, failure
classification, cancellation/shutdown gates, the legacy retry branch, and the
new multiprocess retry-wake test. I made no production or test edits.

The implementation correctly provides the sealed v3 requested/effective/cap
matrix, derives each provider grant from durable `retry_consumed`, uses one
workflow-plus-additional-provider charge equation in the common path, gives
structured repair only the residual grant, conservatively exhausts an
untrusted direct scheduler result, fences retry wakes with a real
multiprocess CAS, preserves legacy total-attempt and 1,000 ms delay behavior,
and contains no Task 10 descriptor work. Four execution paths still violate
the approved unit, evidence, classification, or durable-charge contracts.

## Findings

### Important 1 — Non-structured AI total provider calls are charged as additional attempts, double-counting the initial call

The design fixes the ledger unit as one workflow attempt plus provider attempts
*after* the first provider call. Total-call evidence must therefore be
converted exactly once.

The real isolated worker records total provider calls in
`agent/plugin_agent_worker.py:1531-1548,1638-1670`: its counter starts at zero,
increments for every provider method invocation, and is returned as
`audit.provider_attempts`. Structured output is converted correctly at
`plugins/workflow/executors/ai.py:1095-1116`, but the ordinary text path passes
the same audit value to `validated_provider_retry_count()` unchanged at
`plugins/workflow/executors/ai.py:1125-1131`. That helper explicitly accepts
the value as an internal retry count, and the scheduler then charges it as
`additional_provider_attempts` at `plugins/workflow/scheduler.py:3337-3352`.

Consequently a successful Archon text request with exactly one provider call
reports `provider_attempts=1` and is durably charged `1 workflow + 1
additional = 2`, rather than one total attempt. The new ledger tests bypass the
real AI conversion by returning hand-authored `NodeExecutionResult` metadata,
so they assert the desired unit without detecting the live mismatch.

Normalize the worker's total count once at the v3 AI boundary (while preserving
the legacy protocol contract), and add end-to-end text-result rows for one and
multiple total calls. Include the provider fallback path required by the plan;
the Task 9 diff currently has no fallback-call accounting test.

### Important 2 — Conservative provider counts are relabeled as exact by the scheduler

Missing/invalid evidence must consume the full grant conservatively and remain
identifiable as conservative evidence. The executor already knows this for
runner exceptions, missing audit counts, invalid audit counts, negotiation
failures, and repair failures. For example,
`plugins/workflow/executors/ai.py:428-448` records
`provider_attempts_exact=False`, and lines 1011-1037 and 1173-1180 synthesize a
full-grant count when no exact count exists.

That provenance is not carried into `RetryLedgerGrant.charge()`. The scheduler
extracts only the resulting integer at
`plugins/workflow/scheduler.py:3337-3346`; because the synthesized value is in
range, `plugins/workflow/models.py:344-369` treats it as exact. The evidence
merge then overwrites/creates `provider_attempts_exact=True` at
`plugins/workflow/scheduler.py:3347-3352`, including for a repair result whose
audit explicitly says it was conservative.

Carry a validated count-plus-exactness value across the executor boundary, or
otherwise ensure synthesized counts can never enter the exact branch. Add
runner-exception, missing/invalid ordinary audit, and conservative repair rows
through the real executor plus scheduler, asserting full charge and
`provider_attempts_exact=false`.

### Important 3 — AI execution-contract integrity failures become unknown-outcome reconciliation instead of fatal failures

The approved taxonomy makes sealed/contract drift fatal. It must fail without
replay even under `on_error: all`; it is not an uncertain outward effect.

`AgentNodeExecutor` returns `error_code="execution_integrity"` before provider
launch when the sealed AI entitlement cannot be honored
(`plugins/workflow/executors/ai.py:687-697`). Task 9's fatal set at
`plugins/workflow/scheduler.py:276-290` does not contain this code. On the v3
path, absent `known_no_effect` becomes false at lines 3473-3482, so
`classify_failure()` maps `execution_integrity` to `UNKNOWN_OUTCOME` at lines
315-331. `_persist_result()` then pauses the run for reconciliation at lines
3528-3537.

This misclassifies a known pre-provider contract failure and asks an operator
to reconcile an effect that did not occur. Add the closed contract-drift codes
to the fatal v3 classification (without altering legacy behavior) and test the
real pre-provider entitlement-integrity path under both retry policies,
asserting terminal failure, one workflow charge, zero provider calls, and no
reconciliation interaction.

### Important 4 — The cleanup-failure special path discards the computed durable ledger charge

Task 9 requires every executed v3 workflow attempt to update the durable
combined ledger exactly once. `_persist_result()` initially computes the
correct v3 charge and merges its evidence at
`plugins/workflow/scheduler.py:3322-3353`. It then special-cases
`cleanup_failed` at lines 3359-3365 and calls `RunStore.block_cleanup_failed()`
without the charge or metadata.

`plugins/workflow/store.py:9937-10006` records the cleanup block and desired
status but never updates the node's `retry_consumed` or attempt metadata.
Thus an executor invocation that reached cleanup failure leaves the durable
ledger at its prior value (commonly zero), even though a workflow attempt and
possibly provider attempts were consumed. This breaks the journal authority
and can make later cleanup recovery/restart reason from an undercharged
ceiling.

Persist the charge atomically with the cleanup-failure transition while
retaining its no-replay ownership block. Add v3 deterministic and AI cleanup
failure/restart cases that assert the exact durable charge and that no later
claim/provider launch occurs.

## Verification evidence

All Python tests were run only through `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`.

1. Exact Task 9 gate: `test_phase3_execution_semantics.py`, `test_retry.py`,
   `test_provider_failures.py`, `test_ai_executor.py`,
   `test_parallel_scheduler.py`, `test_coordinator_multiprocess.py`, and
   `test_shutdown_recovery.py` — **7 files, 230 tests passed, 0 failed, no
   retries**.
2. `git diff --check e89c5dce4..dbfce787a` — clean.
3. Worktree was clean before this retained review report was written.

## Final assessment

The common scheduler ledger and retry-wake fencing are sound, and legacy
behavior remains isolated. Task 9 cannot close while the real non-structured
AI protocol double-counts its initial provider call, conservative evidence is
published as exact, contract integrity is routed to reconciliation, and the
cleanup safety transition drops the durable charge. Close these four findings
with real executor-to-scheduler and restart coverage before rereview.
