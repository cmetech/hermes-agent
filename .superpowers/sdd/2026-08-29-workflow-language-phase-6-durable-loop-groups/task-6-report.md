# Task 6 Report: Durable Loop-Group Recovery

Status: COMPLETE

## Changes

- Routed loop-group `until_bash` through the existing ordinary-loop predicate lifecycle: a durable pending decision, authenticated claim, spawn/process callbacks, categorical result, bounded recovery, and process-aware cancellation. The controller still consumes no worker.
- Added an existing journal-obligation reserve for the workerless predicate. The reserve survives restart, never reconstructs as a worker claim, and is released only after the recorded result or cancellation cleanup is durable.
- Preflighted the complete terminal journal transition before writing the outer output or structured typed publication, so exhausted reserve cannot leave an unjournaled effect or success.
- Routed nested approval rejection through the existing nested cancellation terminalizer, preserving controller/body cleanup, capacity, process-tree reaping, stale fences, and exact terminal state.
- Preserved `loop_group_scope` through nested reconciliation and required exact run/group/generation/iteration/body/attempt identity for nested interaction and recovery mutations. Existing top-level interaction shapes remain accepted.
- Added stable private group event families for predicate pending/decision/recovery, iteration completion/decision/pause/next iteration, group success/hard maximum/failure, and cancellation. Applicable events carry authenticated primary-sink body and attempt scope; payloads remain categorical and sanitized.
- Preserved Task 5 children, persistent sessions, scoped publications, structured primary-sink contracts, exact marker stripping, signal precedence, contained Bash, effect reconciliation, journal integrity, stale-write rejection, and ordinary-loop v1-v5 behavior.

## RED Evidence

The focused tests were added before each fix. They failed at the following precise boundaries:

- Predicate lifecycle: no durable pending predicate, process callbacks, or authenticated recorded result existed; restart could replay the Bash predicate.
- Predicate capacity: no obligation reserve existed and recovery reconstituted the workerless predicate as a worker claim.
- Terminal preflight: reserve exhaustion was discovered only after outer output/publication effects had been written.
- Nested rejection: the top-level run terminalized while the controller/body remained running and a parallel Bash process stayed live.
- Nested reconciliation/authentication: cancellation dropped `loop_group_scope`, and a nested interaction without scope was accepted.
- Event contract: stable predicate, iteration, next-iteration, group-success, hard-limit, and cancellation families were absent or lacked applicable sink/body/attempt scope.
- Cancellation cleanup: predicate cancellation released state before process cleanup and retained its journal obligation afterward.
- Event accuracy: a completed group was incorrectly labeled `loop_group_cancelled` during later run cancellation.
- Sanitization: a raw predicate diagnostic containing a private path reached journal evidence.

The initial focused matrix therefore failed before production changes; each listed assertion passed after the corresponding existing lifecycle, transition, cancellation, authentication, or sanitization helper was reused/generalized.

## GREEN Evidence

- Prescribed Task 6 gate: `265 passed, 0 failed, 2 skipped in 101.3s` across the eight required files.
- Task 5 scheduler/store/publication/session compatibility: `360 passed, 0 failed in 42.8s` across nine files.
- Expanded approval/fault/cancel/restart gate: `144 passed, 0 failed in 13.9s` across five files.
- Ruff on all changed Python files: `All checks passed!`.
- `git diff --check`: passed.

## Fault / Recovery / Cancellation Matrix

| Boundary | Evidence |
| --- | --- |
| Predicate intent before spawn | Pending-decision restart test observes no replayed child/predicate and no worker claim |
| Predicate spawn/process/result | Callback lifecycle test records spawn/process identity and categorical result under exact controller scope |
| Predicate restart ambiguity | Before-spawn and after-recorded-result crash tests recover without ambiguous auto-retry |
| Predicate cancellation | Real process cancellation test waits for process-tree cleanup before capacity/reserve release |
| Terminal output/publication | Before-effect reserve exhaustion leaves no output/publication; after-publication crash recovery preserves the single typed bundle |
| Child completion/iteration decision | Scoped child-completion and decision events authenticate controller generation, iteration, body, and attempt |
| Pause/next iteration/outer completion | Event and primary-sink tests prove durable ordering and applicable scope |
| Nested approval rejection | Parallel contained Bash is reaped through the existing cancellation terminalizer before terminal completion |
| Nested reconciliation/auth | Scope is preserved on cancellation and missing/cross-controller nested identity is rejected |
| Hard maximum | Exact 100-iteration boundary records categorical hard failure without an unusable interaction |
| Evidence privacy | Private events exclude prompts, commands, tool data, feedback/output, credentials, environment values, and private paths |
| Ordinary-loop compatibility | Phase 4 loop/interaction suites remain green with existing v1-v5 replay behavior |

## Exact Tests

```text
export HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python
scripts/run_tests.sh tests/plugins/workflow/test_phase6_interactions_recovery.py tests/plugins/workflow/test_fault_injection.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_cancel_node.py tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_phase4_loops.py tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_process_lifecycle_soak.py -v

scripts/run_tests.sh tests/plugins/workflow/test_phase6_scheduler.py tests/plugins/workflow/test_phase6_store.py tests/plugins/workflow/test_phase6_execution_context.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_phase4_loops.py -v

scripts/run_tests.sh tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_approval_races.py tests/plugins/workflow/test_cancel_node.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_fault_injection.py -v
```

## Self-review

- No second executor, scheduler/pool, transition system, action, endpoint, repository, table, public grammar, or core tool was added.
- The workerless controller remains workerless; its predicate uses only the existing Bash execution/process lifecycle and a journal obligation reserve.
- Reserve preflight occurs before terminal file/publication effects; cancellation releases the reserve only after process cleanup is corroborated.
- Nested rejection uses the existing cancellation path rather than maintaining a second terminalizer.
- Completed groups are not mislabeled as cancelled, and stale predicate/group writes are rejected under exact fence and scope.
- Predicate recovery stores only bounded categorical diagnostics; private execution data is not journaled.

Concerns: None known.
