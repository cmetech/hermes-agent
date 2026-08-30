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

The scoped review follow-up added its regressions before follow-up production
edits and ran:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_interactions_recovery.py \
  tests/plugins/workflow/test_phase5_execution_authority_continuity.py \
  -k 'operator_restarts_only_the_failed_current_iteration_child or phase5_redundant_resume_keeps_a_running_claim_unchanged' \
  -q
```

Result: 0 passed, 3 failed. The resume and explicit nested-retry cases left an
attempt-free downstream child `cancelled` after restarting its failed
predecessor. The v5 case showed that a redundant resume of a running run raised
on its active claim instead of returning the existing projection unchanged.

The second scoped re-review expanded the one-worker operator regression with an
independent source child and ran:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_interactions_recovery.py \
  -k 'operator_restarts_only_the_failed_current_iteration_child' -q
```

Result: 0 passed, 2 failed. Both resume and explicit nested retry restored the
failed child and its dependency descendant but left the independent,
attempt-free cancellation stranded, reproducing the running controller wedge.

The final re-review added claimed-but-not-started sibling cases and ran:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase6_interactions_recovery.py \
  -k 'operator_reopens_only_safe_unstarted_attempted_siblings' -q
```

Result: 2 passed, 2 failed. Outward negative cases remained cancelled, while
resume and explicit retry both failed to reopen the exact replay-safe sibling
whose cancelled attempt was corroborated as not started.

The final coherence re-review changed the outward attempted-sibling cases to
require a transactional refusal and reran the same focused command before the
production change. Result: 2 passed, 2 failed. The replay-safe rows completed,
but both outward rows reported `DID NOT RAISE`: resume and retry restarted the
group/controller despite preserving the unreopenable cancelled sibling.

## Root-cause changes

- Routed resume, retry, and abandon safety checks through the existing nested
  projection-state iterator. Live claims and unproven `still_running` or
  `outcome_uncertain` recovery now refuse an eligible operator transition before
  any projection, claim, or reserve mutation. Ineligible resume statuses retain
  their historical no-op/foreground-owner behavior, including v1-v5 running
  runs and `recovery_pending` registry retries. Existing top-level resume/retry
  error contracts remain intact.
- Made resume and explicit retry authenticate the current `group/child` body
  state and preflight the complete current body before mutating any child,
  group/controller state, run error, or journal. A cancelled failure-fallout
  child is reopenable only when it has an empty attempt history, or its exact
  last cancelled attempt is replay-safe and the existing attempt observer
  corroborates `not_started` or `known_stopped`; it must also have no claim,
  recovery, or pending interaction. If any cancelled child fails that test,
  resume or retry refuses with its existing action-specific error contract and
  leaves the failed projection, journal, claims, and reserves byte/semantically
  unchanged. Otherwise the selected failed/interrupted child and all safe
  cancelled fallout are restored from their own dependency states, covering
  both dependency descendants and independent work. Attempt history is
  preserved. Succeeded/skipped siblings, controller generation, and current
  iteration remain unchanged. The outer group is not a retry candidate.
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
  tests/plugins/workflow/test_phase5_execution_authority_continuity.py \
  -k 'operator_restarts_only_the_failed_current_iteration_child or operator_reopens_only_safe_unstarted_attempted_siblings or operator_preserves_unproven_nested_execution_authority or predicate_redispatch_uses_a_fresh_physical_attempt or phase5_redundant_resume_keeps_a_running_claim_unchanged' \
  -q
```

Result after the final re-review: 4 files, 16 tests passed, 0 failed.

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

Result after the final re-review: 6 files, 218 tests passed, 0 failed.

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

Result after the final coherence re-review: 6 files, 231 tests passed, 0 failed.

Static gates:

```bash
.venv/bin/ruff check \
  plugins/workflow/scheduler.py \
  plugins/workflow/store.py \
  tests/plugins/workflow/test_crash_recovery.py \
  tests/plugins/workflow/test_phase5_execution_authority_continuity.py \
  tests/plugins/workflow/test_phase6_interactions_recovery.py \
  tests/plugins/workflow/test_phase6_store.py
git diff --check
```

Result: Ruff reported `All checks passed!`; `git diff --check` reported no
errors.

The final coherence follow-up reran focused Ruff on
`plugins/workflow/store.py` and
`tests/plugins/workflow/test_phase6_interactions_recovery.py`, followed by
`git diff --check`; both remained clean.

## Changed files

- `plugins/workflow/scheduler.py`
- `plugins/workflow/store.py`
- `tests/plugins/workflow/test_crash_recovery.py`
- `tests/plugins/workflow/test_phase5_execution_authority_continuity.py`
- `tests/plugins/workflow/test_phase6_interactions_recovery.py`
- `tests/plugins/workflow/test_phase6_store.py`
- `.superpowers/sdd/2026-08-29-workflow-language-phase-6-durable-loop-groups/adversarial-remediation-batch-2-report.md`

## Commit

`ef809667ad` (`fix(workflow): preserve nested recovery coherence`) — the
initial atomic implementation commit.

`4c82a4392d` (`fix(workflow): restore loop group downstream recovery`) — the
first atomic scoped-review follow-up containing the descendant-fallout reset,
resume ordering fix, and regressions.

`ba43ae89c3` (`fix(workflow): restore parallel loop group fallout`) — the
atomic second re-review follow-up containing the independent-fallout fix and
explicit parameter rows.

`226017277f` (`fix(workflow): reopen safe attempted loop siblings`) — the
atomic attempted-sibling follow-up containing the observed-attempt classifier
fix and positive/negative regressions.

`fix(workflow): refuse unsafe loop group restart` — the atomic final coherence
follow-up containing the transactional body preflight, immutable-refusal
regressions, and this report update. Its final SHA is returned in the task
handoff because a commit cannot embed its own SHA.

## Concerns

None.
