# Phase 3 Task 6 Independent Quality Rereview 1

**Review date:** 2026-08-02  
**Original implementation:** `6942ae0fef908677487691845585d2a775daac59`  
**Review-record commit:** `e056f42e1`  
**Fix implementation:** `73a94ce288496dee8bd3f7af53e906fcefa6389c`  
**Fix implementation tree:** `f7d3d86c887cbcd7e5c2fbf9e7bd290297e7ab4f`  
**Verdict:** PASS

## Severity summary

- Critical: 0
- Important: 0
- Minor: 0

## Scope and evidence reviewed

I reread both original Task 6 review reports, inspected the complete
`e056f42e1..73a94ce2` fix diff and every changed caller/test, and rechecked the
approved state-machine, identity-fence, restart, cache, condition short-circuit,
multi-run finalization, coordinator submission, and zero-attempt requirements.
The rereview made no production or test edits; this retained report is its only
file.

Fresh verification used the required wrapper with flaky file retries disabled:

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
  tests/plugins/workflow/test_typed_publication_recovery.py
```

The wrapper discovered 10 files and reported **206 tests passed, 0 failed** in
16.8 seconds, with no retry or flaky section. Ruff on both changed Python files
passed, and `git diff --check e056f42e1..73a94ce2` passed.

## Original finding closure

### Quality I-1 — alternating transient producers after restart: CLOSED

`RunScheduler._revalidate_retained_output_resolution()` now resolves the
durably retained producer before any other condition or template reference
(`plugins/workflow/scheduler.py:1353-1428`). A matching successful publication
clears the old wait by passing the newly resolved identity to the store. A
continued transient schedules the next observation against the same identity,
and a changed publication reaches the store's existing identity-mismatch
failure. Only after the retained producer is successfully fenced may ordinary
left-to-right evaluation proceed.

This removes the prior dependence on current-pass ordering and process-local
cache contents. New behavior tests exercise condition and non-condition
consumers with `p1`/`p2`, a fresh scheduler with an empty cache, alternating
transient files across wakes, eventual success, matching identity clearing,
and actual changed-identity failure. They assert no attempt/provider/retry
charge and no stale resolution fields after success.

### Quality I-2 — `advance()`/`advance_all()` returned inconsistent running state: CLOSED

After the fair multi-run loop drains or finds no claim, `advance_all()` now
runs `_resolve_graph()` once for every prepared run before returning
(`plugins/workflow/scheduler.py:3717-3723`). This finalizes a terminal
preflight mutation and propagates ordinary graph transitions without claiming
the failed consumer.

The regression matrix covers default `advance()`, explicit `advance_all()`,
and asynchronous coordinator `submit()`. Each path proves the first returned
or persisted result is terminal `failed` with the exact strict code, empty
attempt list, zero retry consumption, zero worker claims, and no executor
allocation.

### Specification I-1 — successful condition reread echoed retained identity: CLOSED

The same revalidation helper closes the specification gap. It derives
`resolved_output_publication_identity(output)` from the newly successful read
and gives that observed identity to `clear_output_resolution()`
(`plugins/workflow/scheduler.py:1418-1427`). It no longer passes the retained
identity back as its own proof. The changed-A-to-B condition regression reaches
terminal `output_reference_integrity`, while the existing matching-A case
clears the wait and becomes ready.

## Regression and quality assessment

- The exact six-observation protocol remains unchanged: 250 ms, 500 ms, 1 s,
  2 s, and 4 s waits, then `output_reference_unavailable` on observation six.
- The retained-producer revalidation occurs only when durable wait state
  exists. It does not eagerly open a reference from a never-reached
  short-circuit branch; a prior observation necessarily reached and failed
  that retained reference against immutable winning predecessors.
- Successful revalidation is cached through the existing bounded positive
  cache, while transient and terminal reads remain negative-cache free.
- Concurrent success/defer/clear calls remain fenced by the run lock and node
  state CAS. A loser observes the resulting pending/ready/waiting/failed state
  without creating a claim or additional attempt.
- The final graph pass cannot hot-loop: it is one bounded pass per prepared run
  after the execution loop. A still-not-due `waiting_resolution` node is
  skipped, and the existing no-claims branch still returns control.
- The fix does not render or substitute values and adds no Task 7, Phase 4/5,
  model-tool, provider-response, path-taking, or new evidence surface.
- Tests execute behavior rather than read source, contain no brittle catalog
  enumeration snapshot, and cover the previously missing production-default
  and coordinator paths.

## Final assessment

Task 6 quality closure passes at production commit `73a94ce2` and tree
`f7d3d86c887cbcd7e5c2fbf9e7bd290297e7ab4f`. All two original quality findings
and the independent specification identity finding are closed, the exact wait
state machine remains intact, and fresh focused/adjacent verification reports
206 passing tests with retries disabled.
