# Phase 3 Task 9 Specification Rereview 1

**Verdict:** CHANGES REQUIRED

**Reviewed HEAD:** `7d4aa28465a1f766930efcda2bcaa6bb1ceaac9d`

**Reviewed tree:** `b8f8fad9d880097474d67edfd6e344355fa2ab55`

**Fix baseline:** `7f8d9289`

**Severity counts:** 0 Critical, 1 Important, 0 Minor

## Scope reviewed

I read the approved Phase 3 retry design, the complete Task 9 plan, both
original Task 9 reviews, and the full `7f8d9289..7d4aa2846` production/test
diff. I traced ordinary and structured provider-count conversion, repair and
fallback grants, exactness provenance from executor through ledger and store,
failure classification, cleanup-failure persistence and restart behavior,
the v3 normalization matrix, both scheduler entrypoints, retry wake/claim
fencing, cancellation boundaries, and the legacy branch. I made no production
or test edits.

The fix closes the original four specification findings except for the real
fallback/provider-call ceiling described below. In particular, ordinary v3
worker totals are converted once to additional provider attempts; structured
and structured-repair totals are also converted once; conservative evidence
remains explicitly inexact and consumes the full grant; cancellation wins
before fatal classification, which wins before outward uncertainty; outward
fatal failures terminate; and cleanup failure atomically retains the durable
charge and attempt metadata without releasing ownership or replaying after
restart.

## Finding

### Important 1 — Core recovery and fallback can still exceed the sealed provider grant before the ledger sees the audit

Task 9 requires one non-multiplying ceiling over every real provider call in a
workflow attempt. Repair and fallback calls must draw from the same sealed
grant, and every tested composition must keep total calls at or below
`effective_total_attempts`.

The worker currently installs the workflow grant as
`agent._api_max_retries = request.max_api_attempts` at
`agent/plugin_agent_worker.py:1530`. That core value is a per-retry-cycle
limit, not a total-call limit. The conversation loop resets `retry_count` to
zero after primary transport recovery at
`agent/conversation_loop.py:4513-4525` and again after activating a fallback at
`agent/conversation_loop.py:4527-4535`. Each reset therefore opens another
full `request.max_api_attempts` cycle. The provider counter added at
`agent/plugin_agent_worker.py:1531-1548` only observes those calls; it does not
prevent a call once the sealed total has been reached.

This is observable in the retained core regression
`tests/run_agent/test_32646_fallback_429_after_timeout.py:222-288`: with
`_api_max_retries = 2`, primary recovery plus fallback performs four provider
calls. Under a v3 workflow grant of two, the worker would similarly execute
four calls, report total `provider_attempts = 4`, and only afterward cause
`validated_provider_total_call_count()` to reject the over-grant count and
charge two conservatively. Durable accounting remains bounded, but the actual
provider side effects and cost have already exceeded the sealed entitlement.

The new Task 9 fallback rows do not exercise the real fallback loop. They use a
fake runner that returns a hand-authored total of two while merely asserting
that `fallback_model` was present on the request
(`tests/plugins/workflow/test_retry.py:453-507`), so they cannot detect retry
cycle resets or an over-grant provider launch.

Required correction: enforce one absolute provider-call budget across every
ordinary worker call, including primary transport recovery, credential/model
fallback, and other retry-cycle resets, without changing the core's legacy
retry/fallback semantics outside sealed v3 execution. Keep structured repair
on the residual grant already calculated by `_repair_or_fail()`. Add a real
worker/conversation-loop composition test showing that recovery and fallback
cannot launch provider call `grant + 1`, and preserve exact/inexact durable
evidence for the calls that did execute.

## Verified closures and retained invariants

- Ordinary v3 worker total `T` is converted exactly once at the executor
  boundary to `T - 1` additional attempts for valid positive worker evidence;
  the one-call and two-call rows persist total charges one and two.
- Structured first calls and exact repair calls use total-call evidence and
  subtract the initial call once. Repair requests receive only
  `granted_provider_attempts - first_provider_attempts`.
- Missing, invalid, exception, repair-failure, and fallback-missing counts
  carry `provider_attempts_exact: false`; `RetryLedgerGrant.charge()` consumes
  the entire remaining grant whenever that flag is false and persists false.
- Exactness and the resulting requested/effective/consumed/remaining evidence
  survive the executor-to-scheduler-to-store boundary.
- Cancellation/shutdown is classified first and cancelled/interrupted results
  are excluded from a new durable charge. Due-wake, post-claim, provider-launch,
  backoff, shutdown, and multiprocess one-winner coverage remain passing.
- Fatal codes, including execution integrity, structured contract/integrity,
  resource, authentication, authorization, and credit failures, are classified
  before outward uncertainty. The real outward entitlement-integrity path is
  terminal under both retry policies with zero provider calls and no
  reconciliation.
- `cleanup_failed` writes retry consumption and bounded attempt metadata in the
  same locked/fenced transition, retains the active ownership claim, and
  remains cleanup-blocked without executor replay after a new scheduler is
  created.
- V3 defaults, explicit values, caps one through five, concurrency fencing,
  deterministic retry requirements, and the durable charge equation remain
  sound. Unversioned and `hermes-legacy` total-attempt and 1,000 ms delay paths
  remain isolated. The diff introduces no Task 10 descriptor work.

## Verification evidence

All Python tests were run only through `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`.

1. Exact Task 9 gate: `test_phase3_execution_semantics.py`, `test_retry.py`,
   `test_provider_failures.py`, `test_ai_executor.py`,
   `test_parallel_scheduler.py`, `test_coordinator_multiprocess.py`, and
   `test_shutdown_recovery.py` — **7 files, 255 tests passed, 0 failed, no
   retries**.
2. Core recovery/fallback diagnostic regression:
   `test_32646_fallback_429_after_timeout.py` — **1 file, 5 tests passed, 0
   failed, no retries**. Its passing four-call assertion with a two-attempt
   cycle is evidence for the remaining v3 integration gap, not a core legacy
   regression.
3. `ruff check` over all six files changed by the fix — **PASS**.
4. `git diff --check 7f8d9289..7d4aa2846` — **PASS**.
5. The worktree was clean before this retained review report was written.

## Final assessment

The four originally reported persistence, provenance, conversion, and
classification defects are otherwise closed. Task 9 cannot close while the
ordinary isolated worker can execute more provider calls than its sealed v3
grant through recovery or fallback retry-cycle resets. Enforce the ceiling at
provider launch and add a real composition regression before the next closure
rereview.
