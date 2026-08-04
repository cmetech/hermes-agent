# Phase 3 Task 8 Independent Quality Rereview 1

**Review date:** 2026-08-02

**Task 8 baseline:** `fa4295b6dc0429c2c79811f28575bb32d52c9b33`

**Original implementation:** `b3be02f9f5247ffc7bc4659ebbaf13df58903230`

**Reviewed HEAD:** `dc585876479e975164f153738348c0ad4fd8ec78`

**Reviewed tree:** `8a180d476826e42b7eefc399a8076935b5d74f6c`

**Verdict:** PASS

## Severity summary

- Critical: 0
- Important: 0
- Minor: 0

## Scope and method

I independently reread the complete Phase 3 design, the complete Task 8 plan
contract, the original Task 8 specification and quality reviews, the full
Task 8 production/test range `fa4295b6d..dc5858764`, and the complete bounded
fix range `b3be02f9f..dc5858764`. I traced:

- sealed v3 execution-semantics authentication and loading;
- both `advance()` and `advance_all()` claim, dispatch, fence-loss, and
  release paths;
- the claim-origin monotonic authority and its exact handoff to execution;
- wall, idle, and provider timeout intersections for initial AI requests,
  structured repair, approval rework, and loop child requests;
- Bash and script preparation, spawn-intent journaling, final launch gates,
  process registration, polling, and recovery ordering;
- real retry/backoff wake behavior and restart handling for zero-effect claims
  and active process identities;
- the exact legacy timeout branch and its separate clock samples;
- the schedule-revalidation test-only repair; and
- the Task 8/Task 9 boundary, prompt/cache boundaries, and excluded Phase 4
  behavior.

Production and test sources were reviewed read-only. This report is the only
file I created.

## Finding closure

### Original specification Important 1 / quality I-1 — claim-origin deadline

**Closed.** Each candidate samples `claim_now` once and passes that same value
to `RunStore.claim_node(..., monotonic_now=claim_now)` and
`_attempt_deadline_budget(..., now=claim_now)`. The resulting absolute budget
is carried in the claimed work item through both scheduler entrypoints.
`_execute_claim()` requires that captured budget for sealed semantics and does
not manufacture a new v3 deadline after dispatch, action-grant handling,
heartbeat startup, or variable preparation.

The new public-entrypoint matrix advances the monotonic clock between a
successful claim and dispatch for both `advance` and `advance_all`, then
asserts the captured budget is already expired. The real executor tests prove
that an expired claim-owned budget reaches no provider or process side effect.

### Original quality I-2 — stale checks before side effects

**Closed.** `sealed_provider_request_for_launch()` resamples the absolute wall
budget immediately before every workflow provider handoff and reconstructs the
immutable request with wall, idle, and provider durations intersected by the
latest remaining wall. The helper gates initial AI requests, structured
repair, and approval rejection rework. Existing loop children pass the same
sealed context into the AI/Bash executors, so they share rather than renew the
attempt deadline.

Bash and script recheck the absolute wall after preparation and again after a
durable spawn intent, immediately before `ManagedProcessTree.spawn()`. An
expiry after intent records `spawn_failed(..., "timeout")` before returning,
so recovery does not mistake a known zero-effect timeout for an uncertain
process launch. Crossing-during-preparation tests cover AI, repair, approval,
loop, Bash, and script and assert no provider/process launch. Exact-boundary
tests also remain green.

### Original specification Important 2 / quality I-3 — batch fence cleanup

**Closed.** `advance_all()` no longer unpacks the stale positional shape in
its fence-loss cleanup. It releases the `NodeClaim` from every accumulated
work item before leaving the scheduling round. The regression acquires one
claim, loses the execution fence on the next candidate, and proves both nodes
are ready, no executor starts, and no worker claim remains.

### Original specification Important 3 / quality I-4 — retry/restart proof

**Closed.** The former helper-only retry test now admits and runs a real v3
workflow through the scheduler. It persists `waiting_retry`, proves an early
wake does nothing, wakes after the durable backoff, claims a second workflow
attempt, and verifies that the later attempt owns a fresh full per-attempt
deadline while the prior deadline is not reused.

The crash-recovery additions prove that a stale zero-effect claim is
classified and interrupted before a replacement claim/budget can exist, and
that an active process identity is observed as still running without another
claim or launch. Together with the existing shutdown/crash suite, these tests
exercise the required recovery-before-reclaim ordering rather than merely
calling a private budget constructor.

### Original quality M-1 — exact legacy boundary sampling

**Closed.** The sealed absolute-deadline branch is profile-gated. The legacy
Bash/script polling expression again samples the absolute budget and elapsed
timeout separately, preserving the pre-Task-8 boundary behavior. Injected
clock regressions prove the second sample can cross the legacy timeout exactly
as before.

## Additional quality audit

- V3 timeout execution reads authenticated `phase3_execution_semantics`; the
  scheduler does not reread current timeout/retry configuration or raw
  authored milliseconds on resume.
- Authored fractional milliseconds and the omitted 120-second deterministic
  default under ceilings below, equal to, and above 120 seconds are exercised
  for both Bash and script.
- A later workflow retry gets a new per-attempt deadline; the backoff is not
  charged to or used to renew the previous attempt.
- Provider repair and approval/loop launches receive remaining wall duration,
  not a fresh full wall duration. AI idle/provider values remain intersected
  with the sealed ceilings and latest wall remainder.
- Bash/script process intent is durable before spawn, a pre-spawn timeout is
  recorded as a known spawn failure, and process identity registration and
  stale-process recovery retain their established ordering.
- The fix does not implement the Task 9 combined retry ledger, add raw/current
  configuration authority, change prompt/tool/history state, add a tool or API
  surface, or introduce Phase 4 loop syntax/semantics.

## Schedule-revalidation repair assessment

The test-only commit `7cb6a00bb` does not weaken production semantics. The old
fixture authored a schema-proven-impossible v3 reference and therefore could
no longer reach the scheduler boundary it intended to test after Task 3's
correct static-admission enforcement. The repaired fixture admits a valid
authenticated direct-dependency reference. The durable-validation test then
injects `WorkflowValidationError` specifically at the scheduled package
revalidation seam and continues to assert the same stable code/path/message,
single terminal event, consumed authorization, zero worker claims, and zero
executor/provider calls. The adjacent revalidation ordering and unexpected
verifier-fault tests now reach and exercise their intended runtime seams with
valid admitted packages. No production validator or scheduler behavior was
changed by this repair.

## Fresh verification evidence

All Python tests were run only through `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`.

1. Task 8 plan gate plus closure surfaces and repaired scheduled validation:

   ```text
   scripts/run_tests.sh \
     tests/plugins/workflow/test_phase3_execution_semantics.py \
     tests/plugins/workflow/test_deadlines.py \
     tests/plugins/workflow/test_ai_executor.py \
     tests/plugins/workflow/test_bash_e2e.py \
     tests/plugins/workflow/test_script_executor.py \
     tests/plugins/workflow/test_shutdown_recovery.py \
     tests/plugins/workflow/test_crash_recovery.py \
     tests/plugins/workflow/test_approval.py \
     tests/plugins/workflow/test_loop_executor.py \
     tests/plugins/workflow/test_schedule_revalidation.py
   ```

   Result: **10 files, 326 tests passed, 0 failed**, with no retry/flaky
   section.

2. Adjacent scheduler, coordinator, retry/provider, and isolated-agent gate:

   ```text
   scripts/run_tests.sh \
     tests/plugins/workflow/test_parallel_scheduler.py \
     tests/plugins/workflow/test_scheduler.py \
     tests/plugins/workflow/test_coordinator.py \
     tests/plugins/workflow/test_coordinator_multiprocess.py \
     tests/plugins/workflow/test_retry.py \
     tests/plugins/workflow/test_provider_failures.py \
     tests/agent/test_plugin_agent.py
   ```

   Result: **7 files, 201 tests passed, 0 failed**, with no retry/flaky
   section. The isolated-agent tests include semantic-idle enforcement that
   ignores transport/stderr heartbeat noise.

3. Ruff passed on all Task 8 production and changed test files.
4. `git diff --check fa4295b6d..dc5858764` passed.
5. Immediately before this report, HEAD and tree matched the requested
   `dc585876479e975164f153738348c0ad4fd8ec78` and
   `8a180d476826e42b7eefc399a8076935b5d74f6c` identities. The only pre-report
   worktree entry was the concurrently produced Task 8 specification rereview
   report; no production or test file was modified by this review.

## Final assessment

The bounded fix closes every original specification and quality finding. The
sealed per-attempt wall authority begins at the successful claim, remains the
same absolute authority through dispatch and nested execution, is checked at
every provider/process side-effect boundary, and does not alter the legacy
timeout path. Retry/backoff and restart safety are now proven through real
scheduler state transitions. Task 8 is ready for controller closure with
**0 Critical, 0 Important, and 0 Minor findings**.
