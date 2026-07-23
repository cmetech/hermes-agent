# Task 10 Report — Isolate and Revalidate Repair-Marked Coordinator Rows

## Outcome

FV-RT-A is fixed without weakening durable evidence or repair authority.
Ordinary and scheduled candidate queries exclude runs whose latest transition
for any run-scoped repair reason is `repair_required`. The correlated latest
transition checks use an always-installed composite index, so unrelated repair
history does not scale either hot query.

Excluded evidence and legacy-policy repairs now have a fair unattended
re-entry path. Each sweep submits healthy work first, then advances a durable
repair-event keyset cursor through at most one eligible repair. Revalidation
uses a 50 ms lock wait, validates the complete journal body within configured
store quotas, validates legacy policy digest and schema when required, repairs
index parity from corroborated evidence, and appends `repair_verified`.
Notification repair remains exclusively owned by the existing outbox cadence.
If cleanup removes a selected repair between its event-log query and probe,
directory resolution now fails closed without aborting the coordinator sweep.

## Strict RED Evidence

The original isolation regressions failed before the first implementation:

```text
scripts/run_tests.sh tests/plugins/workflow/test_scheduled_runs.py -q \
  -k 'active_run_repairs_do_not_consume_coordinator_candidate_pages or
      unrecoverable_migrated_run_does_not_block_healthy_scheduled_sweep'

2 failed, 29 deselected in 0.52s
```

The review follow-up began from this clean RED:

```text
scripts/run_tests.sh \
  tests/plugins/workflow/test_schedule_store_identity.py \
  tests/plugins/workflow/test_scheduled_runs.py \
  tests/plugins/workflow/test_coordinator.py -q \
  -k 'current_store_reinstalls_run_repair_lookup_index_on_every_open or
      coordinator_repair_filters_use_bounded_composite_index_lookups or
      unrecoverable_migrated_run_does_not_block_healthy_scheduled_sweep or
      restored_legacy_effect_policy_is_revalidated_before_submission or
      repair_revalidation_cursor_bypasses_locked_and_corrupt_rows or
      new_leadership_term_restarts_scheduled_paging_at_page_one'

6 failed, 86 deselected in 0.9s
```

Failures proved the missing always-run index, unbounded repair-history scan,
permanent exclusion after restored journal or policy evidence, unfairness
under locked/corrupt rows, and missing cursor reset on leadership change. A
separate RED then proved that a fixed 4 MiB probe ceiling would strand a valid
journal allowed by an 8 MiB configured quota.

Closure review added one deterministic cleanup-race RED:

```text
scripts/run_tests.sh tests/plugins/workflow/test_scheduled_runs.py -q \
  -k disappearing_repair_candidate_does_not_abort_healthy_sweep

1 failed, 36 deselected in 0.5s
```

Healthy submission completed before `run_directory()` raised `KeyError` from
the selected repair probe, proving the exception escaped only because directory
resolution preceded the method's intended fail-closed boundary.

The final selector/quota closure began with:

```text
scripts/run_tests.sh tests/plugins/workflow/test_scheduled_runs.py -q \
  -k 'revalidation_selector_skips_large_unrelated_history_in_one_call or
      failed_revalidation_wraps_while_unrelated_history_keeps_growing or
      revalidation_allows_exact_aggregate_evidence_quota or
      revalidation_rejects_aggregate_evidence_over_run_quota'

3 failed, 1 passed, 37 deselected in 1.0s
```

The selector failed to reach one active repair behind 10,000 unrelated events
and failed to wrap while unrelated history kept growing. The over-cap case
also entered corroboration because three individually small evidence files
exceeded the aggregate run quota. The exact-total boundary passed as the
control. The always-run partial selector-index test separately failed 1/1
before the index existed.

## Implementation

- `repair_events_run_reason_sequence(run_id, reason_code, sequence DESC)` is
  created in the always-run schema block, including current-schema reopen.
  A partial `repair_events_revalidation_sequence` index contains only
  unattended `repair_required` evidence/policy transitions.
- Ordinary and scheduled exclusion predicates retain their existing range,
  keyset, queue-fence, and parity semantics while using indexed latest-event
  lookups.
- One indexed SQL keyset lookup selects only latest-active unattended repairs
  joined to published, nonterminal coordinator-eligible runs. Unrelated and
  notification history never advances the cursor.
- The coordinator processes healthy candidate work before one repair attempt.
  A repaired run becomes eligible on the following sweep.
- Evidence repair reads and rebuilds the full journal, compares it with
  `run.json`, restores the scheduled index from corroborated projection data,
  and records verified transitions.
- Legacy effect-policy repair reuses one digest/schema validator for both node
  classification and unattended revalidation.
- A new leadership term resets the repair cursor. Reaching the event-log tail
  wraps the cursor so still-active failures are eventually retried.
- Mutable run-directory resolution occurs inside the same guarded boundary as
  lock acquisition and evidence reads, so concurrent cleanup is treated as an
  unsuccessful probe and the event cursor still advances.
- Relevant `run.json`, journal, and legacy policy bytes are aggregated against
  `max_run_bytes`, while the journal separately remains bounded by
  `max_journal_bytes`. Equality at the aggregate boundary is allowed.

The lane never deletes, quarantines, synthesizes, or rewrites damaged run
evidence. Oversized corrupt files are bounded by the store's configured
journal/run quotas; valid evidence within those quotas remains eligible.

## Changed Files

- `plugins/workflow/coordinator.py`
- `plugins/workflow/store.py`
- `tests/plugins/workflow/test_coordinator.py`
- `tests/plugins/workflow/test_schedule_store_identity.py`
- `tests/plugins/workflow/test_scheduled_runs.py`
- `docs/upstream-customizations/workflow-orchestration.yaml`
- `.superpowers/sdd/task-10-report.md`

Repository hygiene: `.superpowers/sdd/progress.md` was removed from the Git
index only. Its ignored local contents were preserved and it is not an
implementation artifact.

## Verification

Focused review regressions:

```text
7 passed in 1.5s
```

Closure race plus adjacent repair-lane regressions:

```text
6 passed, 31 deselected in 1.2s
```

Complete focused closure set:

```text
13 passed in 1.8s
```

Affected runtime, migration, index, and coordinator coverage:

```text
scripts/run_tests.sh \
  tests/plugins/workflow/test_schedule_store_identity.py \
  tests/plugins/workflow/test_scheduled_runs.py \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_schema_migrations.py -q

117 passed in 65.3s
```

Additional checks:

- Ruff on all five changed Python/test files: passed.
- Customization-ledger checker: passed.
- `git diff --check`: passed.

## Self-Review

- Query-plan assertions require both correlated levels to use the composite
  index; SQLite opcode counts remain essentially flat after 10,000 unrelated
  repair events for ordinary and scheduled streams.
- The migration regression inserts a complete malformed frame in the middle of
  the journal, proving full-body validation rather than tail-only validation.
- A held oldest lock returns within the 50 ms budget; corrupt rows stay
  excluded while a later valid repair is reached and submitted.
- A valid journal larger than 4 MiB revalidates under its configured 8 MiB
  journal quota.
- Notification repair is skipped by the lane and remains active for outbox
  reconciliation.
- One selector call reaches an active repair behind 10,000 unrelated events in
  fewer than 500 SQLite opcodes; failed repairs wrap and retry while unrelated
  history continues growing.
- Aggregate evidence at exactly `max_run_bytes` is accepted; one byte over is
  rejected before journal corroboration.

## Commit

Original commit: `532c0274c fix(workflow): isolate repair-marked coordinator rows`

Atomic review-fix subject: `fix(workflow): revalidate isolated repair rows`

Cleanup-race follow-up:
`af8f4c4a1 fix(workflow): tolerate disappearing repair rows`

Final selector/quota subject:
`fix(workflow): bound repair revalidation selection`
