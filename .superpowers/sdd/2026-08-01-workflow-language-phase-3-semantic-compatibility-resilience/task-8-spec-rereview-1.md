# Phase 3 Task 8 Specification Closure Rereview 1

**Verdict:** PASS

**Reviewed HEAD:** `dc585876479e975164f153738348c0ad4fd8ec78`

**Reviewed tree:** `8a180d476826e42b7eefc399a8076935b5d74f6c`

**Fix baseline:** `dfe895a80`

**Severity counts:** 0 Critical, 0 Important, 0 Minor

## Scope reviewed

I reread the approved Phase 3 timeout design and complete Task 8 plan, both
first-round Task 8 review reports, and the full `dfe895a80..dc5858764` fix and
test diff, including the separate scheduled-revalidation fixture commit. I
traced both scheduler entrypoints, the claim-to-executor handoff, sealed v3
budget construction, AI main and repair requests, approval rejection rework,
Bash/script launch and polling, loop delegation, retry wake/reclaim behavior,
active-process restart recovery, and the unchanged legacy branches. I made no
production or test edits.

All three specification findings and the overlapping quality findings are
closed. No Task 9 retry-ledger implementation is introduced by this fix.

## Closure evidence

### 1. The sealed budget now starts at the successful claim in both entrypoints

`RunScheduler._attempt_deadline_budget()` accepts an explicit monotonic origin
and builds v3 values only from the authenticated execution-semantics node
projection. `advance()` and `advance_all()` each take one `claim_now` sample,
pass that same sample into `claim_node()`, construct the successful claim's
deadline from it, and carry the resulting immutable budget into
`_execute_claim()`. The executor boundary rejects a v3 claim that arrives
without this captured authority rather than silently creating a later budget.

The public-entrypoint regression covers both `advance` and `advance_all`, moves
the monotonic clock across the exact wall boundary inside claim dispatch, and
proves the carried deadline remains claim-originated and already expired. The
retry integration separately admits a real run, persists `waiting_retry`,
checks the pre-wake boundary, wakes the retry after its backoff, reclaims it,
and proves the later workflow attempt receives a fresh full per-attempt budget
instead of reusing or extending the first.

### 2. Provider and process launches use the latest remaining authority

The shared sealed provider-launch helper samples the absolute attempt budget
immediately before handoff, rejects `remaining <= 0`, and re-intersects wall,
idle, and provider-request values with that latest remaining wall. Both the AI
main request and structured-repair request use this helper, as does approval
rejection rework. Loop children reuse the AI executor and the same attempt
budget, so they receive the identical final handoff gate rather than a new
deadline.

Bash and script retain early validation checks but also recheck the absolute
budget immediately before process launch. If preparation or authenticated
materialization crosses the boundary, no child process is spawned; a recorded
spawn intent is closed with `spawn_failed` where applicable. Crossing tests
cover AI main, structured repair, approval rework, loop child, Bash
substitution, and script runtime preparation. Exact-boundary tests and spawn /
runner call assertions prove zero provider or process launch after expiry.

### 3. `advance_all` fence-loss cleanup releases every acquired claim

The batch cleanup no longer unpacks the obsolete positional tuple shape. It
selects the claim from each complete work item and calls
`release_claim_before_execution()` for every claim acquired before the later
fence loss. Its regression acquires the first of two claims, injects execution
fence loss on the second, and proves both nodes return ready, the worker-claim
table is empty, and no executor starts.

### 4. Retry and restart behavior is exercised through real scheduler state

The former helper-only retry proof is replaced by a real admitted v3 run with
failure persistence, durable retry time, pre-boundary no-wake, post-boundary
wake, promotion, second claim, and successful execution. Its two observed
budgets have distinct claim origins and full sealed durations; retry backoff
is outside the earlier attempt.

Restart coverage now proves both required classifications before later work:

- an expired zero-effect claim is interrupted first, produces no execution,
  and receives a fresh budget only after explicit resume and replacement
  claim; and
- an expired claim with an active outward process remains paused with the
  original attempt identity, one claim event, and no duplicate provider or
  process launch.

### 5. Deterministic timeout and legacy matrices are complete

The scheduler matrix now covers both Bash and script for authored fractional
milliseconds and omitted Archon defaults under subprocess ceilings below,
equal to, and above 120 seconds. It also deliberately mutates live scheduler
configuration after admission and makes `_run_execution_limits()` fail if
called, proving a resumed v3 run consumes only authenticated sealed seconds
and never rereads raw milliseconds or current timeout configuration.

The Bash and script legacy polling expressions again preserve the historical
two monotonic samples: one for the optional absolute budget and a separate one
for elapsed timeout seconds. Injected-clock regressions cross between those
samples and preserve the exact unversioned / `hermes-legacy` boundary behavior.
The new sealed checks remain gated by `sealed_attempt_timeout`.

### 6. The scheduled-revalidation fixture is valid at admission

The fixture now authors `$producer.output.present`, matching the producer's
closed schema, so its Archon v3 package is valid through static admission and
can reach the intended scheduled-promotion boundary. The durable validation
failure test injects `structured_output_field_impossible` only at the
authenticated-command revalidation call; the other tests continue to inject
their own schedule-authority and verifier failures at their existing intended
boundaries. The fixture repair therefore removes the inherited invalid
admission premise without weakening static reference admission or changing
production behavior.

## Out-of-scope audit

The fix does not change the normalized requested/effective retry projection,
combined attempt accounting, retry classification, or provider/workflow
charge ledger reserved for Task 9. It introduces no API/Desktop surface,
provider response exposure, path-taking endpoint, Phase 4 loop/include
semantics, prompt/tool-schema mutation, or current-config authority for resumed
v3 runs.

## Verification evidence

All Python tests were run only through `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`.

Combined Task 8 and adjacent closure gate:

- `test_phase3_execution_semantics.py`
- `test_deadlines.py`
- `test_ai_executor.py`
- `test_bash_e2e.py`
- `test_script_executor.py`
- `test_shutdown_recovery.py`
- `test_crash_recovery.py`
- `test_approval.py`
- `test_loop_executor.py`
- `test_schedule_revalidation.py`

Result: **10 files, 326 tests passed, 0 failed, no retries**.

`git diff --check dfe895a80..dc5858764` also passed.

## Final assessment

Task 8 now implements the approved claim-owned, sealed per-attempt timeout
contract across admission resume, scheduling, provider/process launch, retry,
and restart recovery while preserving exact legacy timeout sampling. The
scheduled-revalidation fixture is valid at initial admission and fails only at
the boundary each test intends to exercise. With **0 Critical, 0 Important,
and 0 Minor findings**, the specification closure review passes.
