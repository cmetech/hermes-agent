# Phase 3 Task 6 Specification Review 1

**Review range:** `f303409881c0079491200b9d9a8272d4f329939e..6942ae0fef908677487691845585d2a775daac59`

**Verdict:** WITH FIXES

**Findings:** 0 Critical, 1 Important, 0 Minor

## Scope and evidence

I reviewed the complete Task 6 plan and the Phase 3 design, then inspected the
full diff plus the surrounding output resolver, scheduler, run-store,
coordinator-selection, catalog, and affected scheduler paths.

Fresh verification used the required wrapper with retries disabled:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_scheduler.py \
  tests/plugins/workflow/test_output_resolution.py \
  tests/plugins/workflow/test_phase3_resolution_waits.py \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_crash_recovery.py \
  tests/plugins/workflow/test_performance_bounds.py
```

The wrapper discovered six existing files and reported **108 passed, 0
failed**, with no retry/flaky section. `git diff --check` also passed.

## Strengths

- `RunStore.defer_output_resolution()` implements the specified six-observation
  sequence exactly: 250 ms, 500 ms, 1 s, 2 s, and 4 s waits followed by
  terminal `output_reference_unavailable` on observation six. The durable
  projection retains `resolution_read_count`, `next_resolution_at`, resume
  state, and the bounded canonical producer identity across journal rebuild.
- Waiting occurs before executor claim. The state is not claimable, pre-due
  wake sweeps append nothing, and both transient and terminal paths retain zero
  workflow attempts, provider allocation, and retry consumption.
- Due wake is fenced by the run lock. The spawn-based multiprocess test proves
  exactly one process records `output_resolution_ready` and the other observes
  no wake.
- Transient host-read failures are not negative-cached. All other strict
  reference codes go to terminal zero-attempt state, and the existing
  post-claim scheduler boundary still marks strict reference failures as
  `archon_terminal_failure` with zero additional provider attempts.
- The transient and exhausted codes have real durable state-machine emitters.
  The catalog bound is centralized at 16 KiB, measured over canonical JSON,
  and its production comment explains the headroom for the remaining approved
  Phase 3 code families.
- The changed scheduler expectation is correct: Task 6 now proves strict
  prompt/command reference failures before provider allocation rather than
  preserving the earlier claimed-attempt behavior.
- The diff stays within Task 6. It does not render/substitute consumer values,
  add Bash spill behavior, add session recovery, or introduce Task 7+ language
  semantics.

## Important finding

### I-1 — A condition reread clears the retained wait identity without comparing it to the successful publication

**Files:**

- `plugins/workflow/scheduler.py:1217-1221`
- `plugins/workflow/scheduler.py:1259-1266`
- comparison with the correct non-condition path at
  `plugins/workflow/scheduler.py:1431-1447`

The condition resolver correctly records each successfully read publication in
`resolved_identities`. After a due wake succeeds, however, the condition path
loads `resolution_producer_identity` and calls
`clear_output_resolution(..., producer_identity=retained)`. That passes the
stored value back to the store, so `RunStore.clear_output_resolution()` merely
compares the retained identity with itself and clears the wait.

It never proves that the newly successful `ResolvedNodeOutput` has that same
identity. If observation one was transient for publication A but the winning
descriptor/result observed after the wake is publication B, the condition can
evaluate and proceed instead of failing as `output_reference_integrity`. This
breaks the Task 6 immutable-producer-publication fence.

The non-condition preflight already implements the required pattern: it clears
only when the retained identity appears among identities actually resolved in
the current pass; otherwise it transitions terminally to
`output_reference_integrity`.

**Required fix:** Apply the same successful-reread identity comparison to the
condition path. Clear only when the retained identity equals an identity
actually produced by this condition evaluation. If it does not, transition the
node to terminal `output_reference_integrity` without claim/provider/retry
charge. Add a scheduler regression that performs a transient condition read
under identity A, wakes, then returns a successful resolved output under
identity B and proves the node fails with zero attempts. Retain a matching-A
success case proving the wait fields clear normally.

## Assessment

The durable wait state machine, timing, exhaustion, restart, fencing, attempt
isolation, catalog wiring, and Task 6 scope are otherwise specification-aligned.
The condition success path must close the publication-identity comparison gap
before Task 6 is accepted.
