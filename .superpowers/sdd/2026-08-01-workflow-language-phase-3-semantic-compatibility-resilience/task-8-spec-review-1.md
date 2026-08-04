# Phase 3 Task 8 Specification Review 1

**Verdict:** CHANGES REQUIRED

**Reviewed HEAD:** `b3be02f9f5247ffc7bc4659ebbaf13df58903230`

**Reviewed tree:** `124150ab0f6a669ba1af8a400441822cf72b7784`

**Task baseline:** `fa4295b6d`

**Severity counts:** 0 Critical, 3 Important, 0 Minor

## Scope reviewed

I read the complete Phase 3 design, the complete Task 8 plan, and the full
`fa4295b6d..b3be02f9f` implementation and test diff. I inspected the sealed v3
execution-semantics read path, both `advance` and `advance_all` claim paths,
the executor boundary, AI provider and structured-repair request construction,
Bash/script launch and polling, retry/recovery ordering, and the unchanged
legacy path. I made no production or test edits.

The implementation correctly:

- reads v3 wall, idle, and provider values from the authenticated retained
  `phase3_execution_semantics` projection and does not call the legacy/current
  timeout resolver for resumed v3 runs;
- converts fractional authored milliseconds once during normalization and
  carries the effective seconds to execution;
- preserves the Archon 120-second deterministic default under ceilings below,
  equal to, and above 120 seconds in the common execution helper;
- intersects AI wall, idle, and provider limits and gives structured repair
  only the remaining wall and provider ceiling;
- rejects AI, Bash, and script launch at the exact exhausted boundary;
- preserves the legacy raw-seconds branches in the executors; and
- keeps retry ledger changes, missing-session recovery, descriptor inheritance,
  API/Desktop work, and Phase 4 behavior out of this task.

Those pieces are not sufficient to close Task 8 because the claim-time
deadline authority and one safety cleanup path remain incorrect, and the
required retry/restart proof was not implemented.

## Findings

### Important 1 — The per-attempt deadline begins after the claim, so dispatch and pre-execution work silently extend the sealed attempt

**Design:**
`docs/superpowers/specs/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience-design.md:334-343`
requires one monotonic `DeadlineBudget` **at each claim**. The workflow attempt
therefore owns one deadline from the claim boundary; dispatch cannot grant a
fresh full duration later.

**Implementation:**
`plugins/workflow/scheduler.py:3512-3579` and
`plugins/workflow/scheduler.py:3749-3856` claim nodes and only later submit the
claimed tuple to `_execute_claim`. `_execute_claim` then marks the node started,
checks cancellation and retry state, consumes an action grant, and starts the
heartbeat at `plugins/workflow/scheduler.py:2889-2955`. Only after all of that
does `_attempt_deadline_budget()` sample a new `self._monotonic()` at lines
2781-2788 and 2957-2961.

Consequently a claim made at monotonic time `T` can wait for dispatch or spend
time in the pre-execution path and still receive
`deadline = later_time + sealed_attempt_wall`. That renews the sealed
per-attempt duration instead of charging the elapsed claimed attempt. It also
means the new exact-boundary executor tests cannot exercise the real scheduler
bug: a delayed scheduler creates a non-expired budget immediately before the
executor.

Capture the monotonic claim instant used by `claim_node` and make that instant
the sole origin for the successful claim's v3 budget in both entrypoints. Add
an integration test that advances monotonic time between claim and executor
dispatch and proves no AI/Bash/script side effect occurs once the claim-owned
budget is exhausted.

### Important 2 — `advance_all` crashes instead of releasing already-acquired claims when the execution fence is lost

**Plan/design boundary:**
Task 8 requires restart/active-claim recovery to classify the in-flight outcome
before another claim (`plan.md:515-521`), and the design requires coordinator
restart not to reset or duplicate an active attempt (`design.md:340-342`). A
fence loss before execution must therefore release every zero-effect claim
cleanly.

**Implementation:**
Task 8 added `execution_semantics[run_id]` to each `advance_all` claim tuple at
`plugins/workflow/scheduler.py:3822-3833`, making it a ten-element tuple. The
fence-loss cleanup at lines 3840-3852 still unpacks nine elements and omits the
new semantics slot.

If one run is claimed and a later claim in the same scheduling round loses its
execution fence, the cleanup raises `ValueError: too many values to unpack`
before `release_claim_before_execution()` runs. The coordinator crashes and
leaves the earlier zero-effect claims stale rather than following the required
fenced recovery ordering. This is a direct Task 8 regression in a safety path.

Update the cleanup shape (prefer a typed claim work item over positional tuple
unpacking) and add an `advance_all` regression with at least one acquired claim
followed by fence loss, asserting all acquired claims are released and no
executor or provider starts.

### Important 3 — The planned retry/backoff and restart timeout tests were not added

**Plan:**
`docs/superpowers/plans/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience.md:503-523`
requires normalization-to-execution coverage for Bash **and script**, a real
workflow retry whose backoff is outside the old budget and whose later claim
gets a fresh budget, restart classification before any later claim, and exact
legacy timeout regression assertions.

**Tests present:**
`tests/plugins/workflow/test_deadlines.py:258-330` exercises only Bash for the
fractional/default subprocess matrix. The test named
`test_archon_workflow_retry_gets_a_fresh_sealed_attempt_budget_after_backoff`
at lines 393-422 does not admit or run a retry, schedule or wake a backoff,
claim a node, or restart a coordinator; it simply calls the private budget
factory twice with times 10 and 50. No Task 8 diff exists in
`test_shutdown_recovery.py` or `test_crash_recovery.py`, and no new assertion
proves the exact legacy path/default/unit contract.

The existing generic crash tests remain valuable, but they do not prove that
v3 timeout state is created only after recovery has classified an active old
claim, nor that a retry wake cannot reuse/reset the previous claim's budget.
This missing integration coverage also allowed Important 1 and Important 2 to
pass the focused gate.

Add the specified end-to-end retry/backoff and restart/fence matrices through
the public scheduler entrypoints, include script in the fractional/default
ceiling matrix, and add explicit legacy seconds/default/path assertions.

## Schedule-revalidation investigation

The four failures in
`tests/plugins/workflow/test_schedule_revalidation.py` are **pre-existing stale
tests, not a Task 8 regression or a timeout coverage gap**.

All four fail inside `_admit_scheduled_impossible_authenticated_command()` at
`assess_package_execution()` before a run is admitted or any scheduler timeout
path executes. The helper deliberately authors a v3 command reference to the
schema-impossible `$producer.output.missing`, but v3 static admission now
correctly rejects that package with `structured_output_field_impossible`.
`git blame` attributes the rejecting static validator to Task 3 commits
`190626ae5`/`680b2df565`; Task 8 changes none of
`schema.py`, `trust.py`, or `test_schedule_revalidation.py`. The four tests must
be repaired to manufacture their post-admission revalidation scenario without
violating the now-required initial static-admission contract, but that is an
inherited test-fixture repair rather than a reason to change Task 8 timeout
production code.

## Verification evidence

All Python tests were run only through `scripts/run_tests.sh` with
`HERMES_PYTHON=../../.venv/bin/python` and
`HERMES_TEST_FILE_RETRIES=0`.

1. Task 8 focused gate:
   `test_phase3_execution_semantics.py`, `test_deadlines.py`,
   `test_ai_executor.py`, `test_bash_e2e.py`, `test_script_executor.py`,
   `test_shutdown_recovery.py`, and `test_crash_recovery.py`
   — **7 files, 213 tests passed, 0 failed, no retries**.
2. Generic isolated-agent timeout/idle boundary:
   `tests/agent/test_plugin_agent.py`
   — **1 file, 68 tests passed, 0 failed, no retries**. This includes the
   existing assertion that worker stderr/heartbeat-like noise does not reset
   the semantic idle deadline.
3. Schedule-revalidation investigation:
   `tests/plugins/workflow/test_schedule_revalidation.py`
   — **1 file, 60 passed, 4 failed** at pre-admission static reference
   validation, as classified above.
4. `git diff --check fa4295b6d..b3be02f9f` — clean.

## Final assessment

Task 8 has the right sealed-value plumbing and executor intersections, but it
does not yet implement the approved claim-owned deadline exactly, and its new
`advance_all` tuple shape breaks fence-loss cleanup. The task also lacks the
explicit retry/backoff/restart and complete deterministic/legacy regression
proof required by the approved plan. Close these three Important findings and
repair the inherited stale schedule test fixture before treating the broader
branch test baseline as green.
