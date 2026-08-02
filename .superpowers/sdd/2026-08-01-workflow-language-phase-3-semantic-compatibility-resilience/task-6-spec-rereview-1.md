# Phase 3 Task 6 Specification Rereview 1

**Review target:** `73a94ce288496dee8bd3f7af53e906fcefa6389c`

**Fix baseline:** `e056f42e11a8113180c4e4ed2978fd4fa347b041`

**Original implementation:** `6942ae0fef908677487691845585d2a775daac59`

**Verdict:** PASS

**Findings:** 0 Critical, 0 Important, 0 Minor

## Scope and fresh evidence

I reread both original Task 6 reports in full, inspected the complete fix diff
and its surrounding scheduler/store/resolver paths, and checked each original
finding against the approved Task 6 and Phase 3 contracts.

Fresh combined verification ran only through the required wrapper with flaky
file retries disabled:

```text
HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 \
  scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_resolution_waits.py \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_coordinator_multiprocess.py \
  tests/plugins/workflow/test_crash_recovery.py \
  tests/plugins/workflow/test_performance_bounds.py \
  tests/plugins/workflow/test_scheduler.py \
  tests/plugins/workflow/test_parallel_scheduler.py \
  tests/plugins/workflow/test_store.py \
  tests/plugins/workflow/test_typed_publication.py \
  tests/plugins/workflow/test_typed_publication_recovery.py \
  tests/plugins/workflow/test_phase3_conditions.py
```

Result: **11 files, 276 tests passed, 0 failed**, with no retry/flaky
summary. Ruff passed on both fix files. `git diff --check` passed for the fix
commit, and the worktree was clean before this rereview report was written.

## Original specification finding closure

### I-1 — Closed: successful condition rereads are fenced to the retained publication

`RunScheduler._revalidate_retained_output_resolution()` now reads the retained
producer before the condition evaluates any other reference. It derives the
identity from the successful `ResolvedNodeOutput` and passes that observed
identity to the store. `RunStore.clear_output_resolution()` therefore compares
the new successful publication to the durable retained publication rather than
comparing the stored identity with itself.

The new A-to-B scheduler regression proves a transient observation under
publication A followed by a successful reread under publication B becomes
terminal `output_reference_integrity`, with no claim, workflow attempt,
provider allocation, or retry charge. The existing transient-A-to-success-A
test proves a matching reread clears every resolution field and advances the
condition normally.

## Original quality finding closure

### I-1 — Closed: retained-producer-first revalidation removes order and restart sensitivity

Both condition evaluation and non-condition preflight invoke the same retained
producer revalidation before their ordinary left-to-right reference traversal.
An unchanged retained producer is either:

- still transient, in which case the same durable wait advances normally;
- successfully reread, in which case its wait is cleared before another
  producer can become the next independent transient; or
- changed/terminal, in which case the consumer fails under the exact strict
  code.

The table test covers condition and template consumers with two direct
producers, transient `p2`, a fresh scheduler with an empty cache, then transient
`p1`. Neither unchanged producer is misclassified as integrity drift; the next
independent wait is durable and zero-attempt, and a final fresh scheduler
clears it successfully. This directly covers restart/process-cache loss and
the original alternating-producer probe.

When no active resolution wait exists, the helper returns before reading any
producer. The scheduler-level `||` and `&&` short-circuit tests remain green,
so unreachable condition references still incur no I/O or cache effect.

### I-2 — Closed: all unbounded scheduler entry points finalize preflight failures in the same call

After all submitted futures settle, `advance_all()` now performs one final
graph resolution/finalization pass for every prepared run before projecting
the result. Because default `advance(run_id)` delegates to `advance_all()`, the
single-run default path inherits the fix, and coordinator `submit()` uses that
same path.

Dedicated tests cover default `advance()`, explicit `advance_all()`, and
coordinator-fenced `submit()`. Each returns or durably publishes terminal
`failed` in the same invocation with exact
`output_reference_integrity`, an empty attempts list, zero retry consumption,
no worker claim, and no executor/provider allocation.

## Unchanged Task 6 contract

- The store still uses exactly five durable delays: 250 ms, 500 ms, 1 s, 2 s,
  and 4 s, with terminal exhaustion on failed observation six.
- A not-yet-due waiter remains excluded from graph transition, runnable node
  selection, claims, provider work, retry accounting, and process-local hot
  polling.
- Due wake remains a single-winner run-lock CAS across processes, and all wait
  fields continue to reconstruct from the journal after run projection loss.
- Transient reads remain absent from the negative cache; every other strict
  resolver failure remains terminal under its stable code.
- The real transient/exhausted emitters and canonical 16 KiB catalog bound are
  unchanged and green.
- The fix adds no Task 7 substitution/rendering, Bash spill behavior, session
  recovery, Phase 4 loop/include semantics, provider response surface, or core
  tool.

## Assessment

The original specification finding and both quality findings are closed. Task
6 now satisfies its immutable-publication, restart, bounded-wait, zero-attempt,
multiprocess fencing, same-call finalization, lazy-condition, and catalog
requirements without expanding scope.
