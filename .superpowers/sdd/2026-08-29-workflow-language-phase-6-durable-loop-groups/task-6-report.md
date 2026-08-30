# Task 6 Report: Durable Loop-Group Recovery

Status: COMPLETE

## Changes

- Routed loop-group `until_bash` through the existing ordinary-loop predicate lifecycle: a durable pending decision, authenticated claim, spawn/process callbacks, categorical result, bounded recovery, and process-aware cancellation. The controller still consumes no worker.
- Added an existing journal-obligation reserve for the workerless predicate. The reserve survives restart, never reconstructs as a worker claim, and is released only after the recorded result or cancellation cleanup is durable.
- Preflighted the complete terminal journal transition before writing the outer output or structured typed publication, so exhausted reserve cannot leave an unjournaled effect or success.
- Routed nested approval rejection through the existing nested cancellation terminalizer, preserving controller/body cleanup, capacity, process-tree reaping, stale fences, and exact terminal state.
- Preserved `loop_group_scope` through nested reconciliation and required exact run/group/generation/iteration/body/attempt identity for nested interaction and recovery mutations. Existing top-level interaction shapes remain accepted.
- Added stable private group event families for predicate pending/decision/recovery, iteration completion/decision/pause/next iteration, group success/hard maximum/failure, and cancellation. Applicable events carry authenticated primary-sink body and attempt scope; payloads remain categorical and sanitized.
- Bound the predicate authority to the exact coordinator execution fence. Preparation, spawn/process callbacks, outcome recording, and recovery now reject a superseded same-owner epoch transactionally.
- Staged the existing controller transition across iteration completion, decision, compatibility, and terminal/next-iteration frames. Restart drains the remaining stages in order; candidate production stays fenced while a stage is pending, and terminal projection cannot outrun its required event.
- Staged cancellation across the group and run event families so restart completes `loop_group_cancelled` then `run_cancelled` exactly once. Predicate journal obligations remain durable between those cuts.
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
- Predicate fence turnover: a superseded same-owner coordinator could still prepare the old authority and journal an outcome after a new epoch acquired leadership.
- Event cardinality: the predicate path emitted `loop_group_iteration_completed` twice for one iteration.
- Completed-to-decision crash: faults after the completion frame permanently omitted the decision family.
- Decision-to-terminal crash: faults after decision permanently omitted the next-iteration, success, or hard-failure family while projection could already be terminal or expose the next body.
- Cancellation-to-run crash: a fault after `loop_group_cancelled` permanently omitted `run_cancelled`.

The round-two focused matrix had seven failures before production changes: four staged-event fault cuts, one cancellation cut, one exact-cardinality contract, and one same-owner fence-turnover contract. It passed `7/7` after the existing lifecycle, journal append, cancellation, authentication, and scheduler helpers were reused/generalized.

## GREEN Evidence

- Prescribed Task 6 gate: `271 passed, 0 failed, 2 skipped in 103.3s` across the eight required files.
- Task 5 scheduler/store/publication/session compatibility: `360 passed, 0 failed in 42.8s` across nine files.
- Expanded approval/fault/cancel/restart/journal/evidence gate: `206 passed, 0 failed, 1 skipped in 14.5s` across nine files.
- Ruff on all changed Python files: `All checks passed!`.
- `git diff --check`: passed.

## Fault / Recovery / Cancellation Matrix

| Boundary | Evidence |
| --- | --- |
| Predicate intent before spawn | Pending-decision restart test observes no replayed child/predicate and no worker claim |
| Predicate spawn/process/result | Callback lifecycle test records spawn/process identity and categorical result under exact controller scope |
| Predicate restart ambiguity | Before-spawn and after-recorded-result crash tests recover without ambiguous auto-retry |
| Predicate fence turnover | Same-owner epoch turnover rejects stale preparation and result journaling; the winner recovers the exact fenced authority |
| Predicate cancellation | Real process cancellation test waits for process-tree cleanup before capacity/reserve release |
| Terminal output/publication | Before-effect reserve exhaustion leaves no output/publication; after-publication crash recovery preserves the single typed bundle |
| Child completion/iteration decision | Scoped child-completion and decision events authenticate controller generation, iteration, body, and attempt |
| Completion/decision/next/terminal cuts | Parameterized append faults prove restart completes each staged family exactly once and in order; N+1 is blocked until its visibility frame is durable |
| Pause/next iteration/outer completion | Exact-cardinality event and primary-sink tests prove durable ordering and applicable scope |
| Nested approval rejection | Parallel contained Bash is reaped through the existing cancellation terminalizer before terminal completion |
| Cancellation event split | Restart after the group cancellation frame completes `run_cancelled` exactly once while retaining predicate obligations until cleanup |
| Nested reconciliation/auth | Scope is preserved on cancellation and missing/cross-controller nested identity is rejected |
| Hard maximum | Exact 100-iteration boundary records categorical hard failure without an unusable interaction |
| Evidence privacy | Private events exclude prompts, commands, tool data, feedback/output, credentials, environment values, and private paths |
| Ordinary-loop compatibility | Phase 4 loop/interaction suites remain green with existing v1-v5 replay behavior |

## Exact Tests

```text
export HERMES_PYTHON=/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/.venv/bin/python
scripts/run_tests.sh tests/plugins/workflow/test_phase6_interactions_recovery.py tests/plugins/workflow/test_fault_injection.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_cancel_node.py tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_phase4_loops.py tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_process_lifecycle_soak.py -v

scripts/run_tests.sh tests/plugins/workflow/test_phase6_scheduler.py tests/plugins/workflow/test_phase6_store.py tests/plugins/workflow/test_phase6_execution_context.py tests/plugins/workflow/test_parallel_scheduler.py tests/plugins/workflow/test_typed_publication.py tests/plugins/workflow/test_typed_publication_recovery.py tests/plugins/workflow/test_persistent_session_recovery.py tests/plugins/workflow/test_phase4_loop_interactions.py tests/plugins/workflow/test_phase4_loops.py -v

scripts/run_tests.sh tests/plugins/workflow/test_approval.py tests/plugins/workflow/test_approval_races.py tests/plugins/workflow/test_cancel_node.py tests/plugins/workflow/test_crash_recovery.py tests/plugins/workflow/test_fault_injection.py tests/plugins/workflow/test_evidence_api.py tests/plugins/workflow/test_journal_reserve_fanout.py tests/plugins/workflow/test_phase5_inline_approval_convergence.py tests/plugins/workflow/test_phase5_public_run_evidence_contract.py -v
```

## Self-review

- No second executor, scheduler/pool, transition system, action, endpoint, repository, table, public grammar, or core tool was added.
- The workerless controller remains workerless; its predicate uses only the existing Bash execution/process lifecycle and a journal obligation reserve.
- Reserve preflight occurs before terminal file/publication effects; cancellation releases the reserve only after process cleanup is corroborated.
- Nested rejection uses the existing cancellation path rather than maintaining a second terminalizer.
- Completed groups are not mislabeled as cancelled, and stale predicate/group writes are rejected under exact fence and scope.
- Iteration completion has exact cardinality; decision, next-iteration, success, hard-failure, and cancellation families recover deterministically from every named append cut.
- The controller transition marker is private staged state under the existing run lock and `_append_locked`; it is not a second transition engine or public surface.
- Structured publications created before an injected append crash are accepted only when the exact staged success authority and succeeded output attempt corroborate them.
- Predicate recovery stores only bounded categorical diagnostics; private execution data is not journaled.

Concerns: None known.
