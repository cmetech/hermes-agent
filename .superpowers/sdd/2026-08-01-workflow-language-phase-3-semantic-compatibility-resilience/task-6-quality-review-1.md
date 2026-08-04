# Phase 3 Task 6 Independent Quality Review 1

**Review date:** 2026-08-02  
**Baseline:** `f30340988`  
**Implementation:** `6942ae0fef908677487691845585d2a775daac59`  
**Implementation tree:** `29b4ccb64f6695d8451616bbf855c5ec5c8b2465`  
**Verdict:** CHANGES REQUIRED

## Severity summary

- Critical: 0
- Important: 2
- Minor: 0

## Scope and evidence reviewed

I read the complete repository `AGENTS.md`, the complete approved Phase 3
design, the complete implementation plan and Task 6 requirements, the full
`f30340988..6942ae0f` diff, every changed production module and caller, the
run-store projection/journal rebuild and CAS paths, scheduler single-run and
fair multi-run entry points, coordinator wake selection, output-resolution
cache and publication identity handling, the durable-code catalog, and all new
and adjacent tests. The review made no production or test edits; this report is
the only file it created.

The following commands were run with flaky file retries disabled:

1. Exact Task 6 gate:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_phase3_resolution_waits.py tests/plugins/workflow/test_phase3_code_catalog.py tests/plugins/workflow/test_coordinator_multiprocess.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_performance_bounds.py`
   — 5 files, 72 tests passed, 0 failed.
2. Adjacent store, scheduler, multiprocess scheduling, and typed-publication
   gate:
   `HERMES_PYTHON=../../.venv/bin/python HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh tests/plugins/workflow/test_scheduler.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_store.py tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_output_resolution.py`
   — the nonexistent `test_output_resolution.py` selector contributed no file;
   the 5 discovered files ran 128 tests, all passed.
3. Ruff on every changed Python file — clean.
4. `git diff --check f30340988..6942ae0fef908677487691845585d2a775daac59`
   — clean.

I also ran two read-only production-path probes in temporary directories. One
restarted the scheduler between transient reads from two referenced producers;
the other used the normal `advance(run_id)` entry point for a terminal
preflight failure. Their exact outcomes are recorded below.

## Important findings

### I-1 — A restart can falsely report producer-integrity drift when a different referenced producer is transient

The durable node state retains only one `resolution_producer_identity`. On a
subsequent observation, the scheduler clears that identity before deferring a
different producer only if it has already successfully resolved the retained
producer during the current left-to-right pass
(`plugins/workflow/scheduler.py:1229-1250` and
`plugins/workflow/scheduler.py:1404-1421`). If the newly transient reference is
visited first, `resolved_identities` cannot contain the retained producer.
`RunStore.defer_output_resolution()` then interprets the two different producer
identities as publication drift and terminally fails the consumer with
`output_reference_integrity` (`plugins/workflow/store.py:10427-10439`).

This is reachable after restart because the successful-output cache is
process-local. The review probe used a condition with direct references to
`p1` and `p2`. Observation one successfully read `p1` and transiently failed
`p2`, durably retaining `p2`. After waking and constructing a fresh scheduler,
observation two transiently failed `p1` before reaching `p2`. Neither winning
publication changed, but the result was:

```text
{'first_wait_producer': 'p2',
 'second_state': 'failed',
 'second_error': {'code': 'output_reference_integrity',
                  'message': 'output reference producer identity changed during resolution',
                  'node_id': 'consumer'}}
```

The same order-dependent logic exists in condition evaluation and non-condition
preflight. It violates restart safety and the meaning of
`output_reference_integrity`: ordinary, independent host-read transience must
not masquerade as a changed winning publication.

**Required remediation:** Fence wait state by the specific reference/producer
being retried, or deterministically revalidate/clear the retained producer
before resolving any other reference. Add RED tests for both condition and
non-condition consumers with two direct producers, alternating transient reads
across a fresh scheduler/restart, and cache eviction. Prove unchanged producer
identities continue through the bounded wait protocol, while an actual retained
producer identity change remains terminal integrity failure.

### I-2 — Normal `advance()` returns a nonterminal run after preflight terminally fails its last node

`advance(run_id)` without `max_nodes` delegates to `advance_all([run_id])`
(`plugins/workflow/scheduler.py:3202-3205`). `advance_all()` resolves the graph,
then performs strict non-condition preflight while building candidates. A
terminal preflight error changes the ready node to `failed`, returns no
candidate, and reaches the no-claims break
(`plugins/workflow/scheduler.py:3474-3490` and
`plugins/workflow/scheduler.py:3615-3617`). Unlike the bounded `max_nodes` path,
the multi-run path returns immediately without a final `_resolve_graph()` or
`finalize_if_complete()` pass (`plugins/workflow/scheduler.py:3622-3629`).

The review probe pre-completed a producer, forced
`output_reference_integrity` while preflighting the only consumer, and invoked
the production-default entry point twice. It observed:

```text
{'first_status': 'running', 'first_consumer': 'failed',
 'second_status': 'failed', 'second_consumer': 'failed'}
```

Thus the node failure is durable and zero-attempt, but the first caller receives
an internally inconsistent run (`status: running` with every node terminal).
A background coordinator eventually repairs it on another sweep; a foreground
caller can hand back a run that appears stuck and needs an unrelated second
advance. The current Task 6 preflight tests all call
`advance(..., max_nodes=1)`, whose final graph pass masks this defect.

**Required remediation:** Re-resolve/finalize each mutated run after preflight
and before `advance_all()` returns, without claiming or charging an attempt.
Add RED coverage for default `advance()`, explicit `advance_all()`, and the
coordinator submission path, asserting one call returns terminal `failed`, the
exact strict code, zero attempts/retry consumption, and no provider/executor
allocation.

## Positive findings

- The store implements the requested 250 ms, 500 ms, 1 s, 2 s, and 4 s delay
  sequence and terminally exhausts on the sixth failed observation without an
  off-by-one error.
- `waiting_resolution` is excluded from claim eligibility and dependency graph
  transitions. The scheduler's no-claims branch exits rather than polling the
  store in a process-local hot loop; the earlier static hot-loop concern was
  disproved by a production-path probe.
- Run-lock CAS makes due-wake selection single-winner across processes, and
  full-projection journal frames preserve count, due time, resume state, and
  producer identity across run.json loss/rebuild.
- Transient host read failures are not inserted as negative cache entries and
  consume neither executor attempts nor provider budget. Non-transient strict
  errors are terminal zero-attempt transitions.
- Producer identities and journal diagnostics are exact-field and byte bounded;
  deferred events expose only the identity digest rather than output contents
  or paths.
- The central catalog additions are additive, behavior-linked, and constrained
  by the 16 KiB projected catalog ceiling. No change-detector enumeration count
  or source-reading test was added.
- The diff adds no Phase 4/5 language behavior, core tool, raw provider
  response, path-taking endpoint, or Task 7 rendering/substitution authority.

## Final assessment

Task 6 is not ready to hand off. Its store-level backoff/CAS protocol, bounded
catalog, cache-negative behavior, and multiprocess wake mechanics are sound,
and 200 focused/adjacent tests pass without retry. However, the scheduler's
single retained producer fence is restart- and order-sensitive for legitimate
multi-reference workflows, and its normal multi-run entry point does not
finalize terminal preflight mutations before returning. Both Important findings
need a bounded fix round, genuine RED coverage, and fresh focused verification.
