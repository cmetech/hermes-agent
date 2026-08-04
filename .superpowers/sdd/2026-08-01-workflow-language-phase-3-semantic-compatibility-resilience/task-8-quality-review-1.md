# Phase 3 Task 8 Independent Quality Review 1

**Review date:** 2026-08-02

**Task 8 baseline:** `fa4295b6dc0429c2c79811f28575bb32d52c9b33`

**Implementation:** `b3be02f9f5247ffc7bc4659ebbaf13df58903230`

**Reviewed tree:** `124150ab0f6a669ba1af8a400441822cf72b7784`

**Verdict:** CHANGES REQUIRED

## Severity summary

- Critical: 0
- Important: 4
- Minor: 1

## Scope and independent verification

I read the repository development guide, the approved Phase 3 timeout design,
the complete Task 8 plan contract, and the Task 8 implementation diff. I
traced sealed execution-semantics loading, both `advance()` and
`advance_all()` claim paths, deadline construction, AI request and repair
intersections, loop children, approval rework, Bash/script process launch and
termination, retry wake behavior, claim/process recovery, legacy gates, and
the added and adjacent tests. Production and test sources were reviewed
read-only; this report is the only file I created.

Fresh verification used only `scripts/run_tests.sh` with the repository Python
and file retries disabled:

1. Exact Task 8 plan gate:

   ```text
   HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
     scripts/run_tests.sh \
     tests/plugins/workflow/test_phase3_execution_semantics.py \
     tests/plugins/workflow/test_deadlines.py \
     tests/plugins/workflow/test_ai_executor.py \
     tests/plugins/workflow/test_bash_e2e.py \
     tests/plugins/workflow/test_script_executor.py \
     tests/plugins/workflow/test_shutdown_recovery.py \
     tests/plugins/workflow/test_crash_recovery.py
   ```

   The wrapper reported **7 files, 213 passed, 0 failed**, with no retry/flaky
   section.

2. Adjacent scheduler, coordinator, approval, and loop gate:

   ```text
   HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
     scripts/run_tests.sh \
     tests/plugins/workflow/test_parallel_scheduler.py \
     tests/plugins/workflow/test_scheduler.py \
     tests/plugins/workflow/test_coordinator.py \
     tests/plugins/workflow/test_approval.py \
     tests/plugins/workflow/test_loop_executor.py
   ```

   The wrapper reported **5 files, 126 passed, 0 failed**, with no retry/flaky
   section.

3. Isolated-agent wall/idle/provider boundary gate:

   ```text
   HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
     scripts/run_tests.sh tests/agent/test_plugin_agent.py
   ```

   The wrapper reported **1 file, 68 passed, 0 failed**, with no retry/flaky
   section. Existing idle tests confirm that stderr/transport noise does not
   count as semantic progress.

4. Scheduled-revalidation reproduction:

   ```text
   HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
     scripts/run_tests.sh tests/plugins/workflow/test_schedule_revalidation.py
   ```

   The wrapper reported **60 passed, 4 failed**. The exact four failures and
   their classification are recorded below.

5. Ruff passed on all ten Task 8 production/test files.
6. `git diff --check fa4295b6d..b3be02f9` passed. The worktree was clean
   before this report was added.

## Important findings

### I-1 — The sealed attempt deadline starts after the claim instead of at the claim

`advance()` and `advance_all()` sample monotonic time while calling
`claim_node()` (`plugins/workflow/scheduler.py:3514-3525,3769-3784`), but that
sample is discarded. `_execute_claim()` later marks the attempt started,
checks cancellation and retry state, consumes an action grant, starts its
heartbeat thread, and only then calls `_attempt_deadline_budget()`, which takes
a new monotonic sample (`plugins/workflow/scheduler.py:2889-2961`).

The design requires the per-attempt wall budget to begin at claim. With the
current ordering, executor-pool delay plus claim/start/grant bookkeeping is
free time. A short sealed attempt can sit claimed for longer than its entire
budget and still receive a fresh full deadline immediately before variable
construction. This also means coordinator scheduling delay can extend each
workflow retry independently beyond its sealed duration.

Capture one monotonic sample for each claim, use it both as `monotonic_now`
for claim authority and as the budget origin, and carry the resulting budget
or its origin through both scheduler paths. Add a test that delays dispatch
after a successful claim and proves the executor observes the already-consumed
budget rather than a fresh duration.

### I-2 — Deadline checks are stale before provider/process side effects

The new Bash and script checks occur only at executor entry
(`plugins/workflow/executors/bash.py:33-40`,
`plugins/workflow/executors/script.py:143-150`). They then perform directory
creation, strict substitution/resource planning, environment construction,
output setup, spawn-intent journaling, and optional stdin materialization
before calling `ManagedProcessTree.spawn()` without another deadline check
(`bash.py:41-90`, `script.py:151-230`). If that preparation crosses the exact
sealed boundary, the process still starts and can perform an outward effect
before the polling loop terminates it.

The AI path has the same stale-duration shape: it computes `wall_timeout`,
idle, and provider durations at lines 818-847, then resolves MCP resources,
inline agents, request options, and the complete request before launching the
runner at lines 987-990. The runner receives a duration, not the absolute
attempt deadline, so work between the sample and child enforcement extends the
attempt. Approval rejection rework is worse: it permits a zero remaining wall
at lines 124-171 and proceeds to `agent_runner.run()` at lines 208-209; real
request validation can surface this as an executor crash/validation error
instead of the stable timeout outcome.

Recheck the absolute budget immediately before every provider/process launch
and fail without side effects at `remaining <= 0`. Provider/repair handoff
must be derived from the latest remaining wall and must not restart a longer
clock after process setup. Add crossing-during-preparation tests, not only
already-expired-at-entry tests, for Bash, script, AI, approval rework, and the
loop child paths that reuse them.

### I-3 — `advance_all()` crashes while releasing claims after execution-fence loss

Task 8 added `execution_semantics[run_id]` to each batch claim tuple, making it
ten fields (`plugins/workflow/scheduler.py:3822-3833`), but the fence-loss
cleanup still unpacks the old nine-field shape
(`plugins/workflow/scheduler.py:3840-3852`). If one claim succeeds and a later
claim reports execution-fence loss, cleanup raises `ValueError: too many
values to unpack` before `release_claim_before_execution()` runs.

This is a normal failover boundary, not a malformed-input edge case. It leaves
zero-effect claims stranded until lease recovery, aborts the batch call, and
can delay the successor coordinator or route claims through unnecessary crash
recovery. Include the semantics element in the unpack (or replace positional
claim tuples with a named structure) and add an `advance_all()` test that
claims one node, loses the fence on the next claim, and proves every prior
claim is released without executor launch.

### I-4 — Retry/restart coverage does not execute the behavior required by Task 8

`test_archon_workflow_retry_gets_a_fresh_sealed_attempt_budget_after_backoff`
calls the private `_attempt_deadline_budget()` helper twice with two clock
values (`tests/plugins/workflow/test_deadlines.py:393-422`). It never admits a
run, records a retryable timeout, persists a backoff, wakes it, claims a second
attempt, or checks that no provider/process call occurs under the prior
budget. No Task 8 change was made to shutdown/crash tests, so their green
result proves the pre-existing recovery model but not the new deadline's
interaction with active claim/process recovery.

The omitted integration coverage allowed I-1 and I-3 to pass: there is no
delay between claim and budget creation and no fence-loss-after-first-batch-
claim scenario. Replace the helper-only proof with a real scheduler retry
transition and add restart cases for zero-effect claimed work and active
process work. Assert fresh later-attempt duration, prior-attempt backoff
separation, no duplicate spawn/provider call, and recovery classification
before any later claim.

## Minor finding

### M-1 — The nominally legacy branch changed its exact boundary sampling

Before Task 8, Bash and script legacy timeout polling sampled
`context.monotonic()` separately for the absolute budget and elapsed fallback.
The new `sealed_attempt_timeout == False` branch samples once into `now` and
uses that value for both checks (`plugins/workflow/executors/bash.py:109-117`,
`plugins/workflow/executors/script.py:258-266`). If the boundary is crossed
between the former two samples, legacy execution now receives one additional
poll interval (normally about 10 ms) before termination.

The phase contract requires exact unversioned and `hermes-legacy` behavior,
including existing code paths. Keep the old legacy expression byte-for-byte
or otherwise demonstrate exact equivalence with injected-clock regression
tests; confine the new absolute-deadline branch to sealed v3 attempts.

## Schedule-revalidation failure classification

The four reproduced failures are:

- `test_scheduled_package_validation_matches_immediate_durable_failure[advance]`
- `test_scheduled_package_validation_matches_immediate_durable_failure[advance_all]`
- `test_scheduled_package_validation_revalidates_before_terminal_mutation`
- `test_scheduled_package_validation_propagates_unexpected_verifier_fault`

All four fail in fixture construction at
`assess_package_execution()`/`compute_package_digest()`, before Task 8
scheduler execution. Their Archon v3 command references
`$producer.output.missing` while the producer schema closes
`additionalProperties` and declares only `present`, so Task 3's static
admission correctly raises `structured_output_field_impossible`.

This is a **stale pre-Task8 test fixture**, not a Task 8 integration failure:
the Git blob IDs for `test_schedule_revalidation.py`, `schema.py`, and
`trust.py` are identical at Task 8 baseline `fa4295b6d` and implementation
`b3be02f9`. The fixture was authored before the Task 3 strict static-reference
contract and now tries to create a package that cannot legitimately reach its
intended scheduler-time validation boundary. The test should be repaired with
a package that is valid at admission but becomes invalid only at the intended
sealed package-preparation step. The active red file still needs closure
before the branch's final canonical gate, but it is not charged as an
additional Task 8 finding.

## Passing contract audit

- Sealed `resources.json` is parsed canonically and v3 execution semantics are
  verified before use; v3 timeout values are read from the sealed node map and
  the semantics node lookup itself is O(1).
- Authored fractional milliseconds and omitted Bash defaults below, at, and
  above 120 seconds reach scheduler contexts correctly. Resumed v3 timeout
  fields do not call `_run_execution_limits()` or reinterpret raw authored
  milliseconds.
- AI initial and structured-repair requests intersect the provider ceiling
  with the remaining wall at the sample they take, and loop iterations share
  one budget rather than creating a child budget per provider iteration.
- Each later `_execute_claim()` currently constructs a distinct budget, so
  retry backoff is outside the prior budget and there is no cross-retry total
  deadline. I-1 concerns the origin of each such budget, not accidental budget
  reuse.
- Existing stale-claim and active-process recovery runs before ready-node
  claims in both paths, and the unchanged crash/shutdown tests pass. No Task 9
  combined-ledger implementation was introduced.
- No current config, raw v3 source timeout, prompt prefix, tool schema, raw
  provider response, API surface, path-taking endpoint, Phase 4 loop/include,
  or literal-main behavior is introduced by the Task 8 diff.

## Final verdict

Task 8 correctly threads sealed normalized timeout values into its central
budget and focused tests, but the deadline does not yet start at claim, launch
gates can use stale remaining time, and batch fence-loss cleanup is broken.
The planned retry/restart proof is also helper-level rather than behavioral.
With **0 Critical, 4 Important, and 1 Minor** finding, Task 8 requires a
bounded fix round and fresh verification before closure.
