# Adversarial remediation batch 2 implementation report

## Scope

Closed AR-04 through AR-06 only: scope-aware operator recovery for durable loop
groups, fresh physical predicate redispatch attempts, and nested live-execution
guards. AR-07 and AR-08 artifact accounting was not touched.

## RED evidence

The focused regressions were added before production edits and run with:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_interactions_recovery.py \
  tests/plugins/workflow/test_phase6_store.py \
  tests/plugins/workflow/test_crash_recovery.py \
  -k 'operator_restarts_only_the_failed_current_iteration_child or operator_refuses_unproven_nested_execution_without_releasing_authority or predicate_redispatch_uses_a_fresh_physical_attempt' \
  -q
```

Result: 0 passed, 9 failed. The failures reproduced all three findings:

- resume reset the outer group while leaving its current controller/body
  terminal, and explicit `group/flaky` retry found no public retry candidate;
- resume, retry, and abandon did not refuse all live or outcome-uncertain body
  children while retaining their worker-claim and journal-reserve authority;
- a real Bash predicate recovery reused the existing `decision/attempt`
  directory and raised `FileExistsError` before reaching a durable decision.

The same focused command passed 9 tests after the production changes.

## Root-cause changes

- Routed resume, retry, and abandon safety checks through the existing nested
  projection-state iterator. Live claims and unproven `still_running` or
  `outcome_uncertain` recovery now refuse the operator transition before any
  projection, claim, or reserve mutation. Existing top-level resume/retry error
  contracts remain intact.
- Made resume and explicit retry authenticate the current `group/child` body
  state, reset only failed/interrupted children with replay-safe stopped-process
  evidence, and restore the existing outer/controller state to `running`.
  Succeeded/skipped siblings, controller generation, current iteration, and
  attempt history remain unchanged. The outer group is not a retry candidate.
- Preserved the one durable predicate claim, execution fence, callbacks, and
  obligation-journal reserve while assigning every authorized Bash dispatch a
  fresh physical attempt ID and contained directory beneath the iteration's
  decision publication root. Recovery no longer deletes or reuses a completed
  physical attempt directory, remains workerless, and does not replay body
  work or a recorded predicate result.
- Updated existing predicate authority/fence assertions to distinguish the
  fresh physical executor attempt from the durable predicate obligation used
  for fence and journal correlation.

## Verification

Focused GREEN:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_interactions_recovery.py \
  tests/plugins/workflow/test_phase6_store.py \
  tests/plugins/workflow/test_crash_recovery.py \
  -k 'operator_restarts_only_the_failed_current_iteration_child or operator_refuses_unproven_nested_execution_without_releasing_authority or predicate_redispatch_uses_a_fresh_physical_attempt' \
  -q
```

Result: 3 files, 9 tests passed, 0 failed.

Required Phase 6 recovery gate:

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_store.py \
  tests/plugins/workflow/test_phase6_interactions_recovery.py \
  tests/plugins/workflow/test_phase6_scheduler.py \
  tests/plugins/workflow/test_crash_recovery.py \
  tests/plugins/workflow/test_fault_injection.py \
  tests/plugins/workflow/test_cancel_node.py -q
```

Result: 6 files, 212 tests passed, 0 failed.

Required historical compatibility gate:

```bash
scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_execution_semantics.py \
  tests/plugins/workflow/test_phase4_loops.py \
  tests/plugins/workflow/test_phase5_execution_authority_continuity.py \
  tests/plugins/workflow/test_retry.py \
  tests/plugins/workflow/test_store.py \
  tests/plugins/workflow/test_shutdown_recovery.py -q
```

Result: 6 files, 230 tests passed, 0 failed.

Static gates:

```bash
.venv/bin/ruff check \
  plugins/workflow/scheduler.py \
  plugins/workflow/store.py \
  tests/plugins/workflow/test_crash_recovery.py \
  tests/plugins/workflow/test_phase6_interactions_recovery.py \
  tests/plugins/workflow/test_phase6_store.py
git diff --check
```

Result: Ruff reported `All checks passed!`; `git diff --check` reported no
errors.

## Changed files

- `plugins/workflow/scheduler.py`
- `plugins/workflow/store.py`
- `tests/plugins/workflow/test_crash_recovery.py`
- `tests/plugins/workflow/test_phase6_interactions_recovery.py`
- `tests/plugins/workflow/test_phase6_store.py`
- `.superpowers/sdd/2026-08-29-workflow-language-phase-6-durable-loop-groups/adversarial-remediation-batch-2-report.md`

## Commit

`fix(workflow): preserve nested recovery coherence` — the atomic commit
containing the implementation, regressions, compatibility assertion updates,
and this report. Its final SHA is returned in the task handoff because a commit
cannot embed its own SHA.

## Concerns

None.
