# Task 10 Report — Isolate Repair-Marked Coordinator Rows

## Outcome

FV-RT-A is fixed without changing durable evidence or repair authority.
Ordinary and scheduled coordinator candidate queries now exclude rows whose
latest transition for any run-scoped repair reason is `repair_required`.
Filtering happens inside the bounded SQL query, before `LIMIT`, so a damaged
row cannot consume a page slot, pin a keyset cursor, or abort parity checks for
healthy peers. A later `repair_verified` transition restores eligibility.

## Strict RED Evidence

Production code was unchanged when these regressions were first run:

```text
scripts/run_tests.sh tests/plugins/workflow/test_scheduled_runs.py -q \
  -k 'active_run_repairs_do_not_consume_coordinator_candidate_pages or
      unrecoverable_migrated_run_does_not_block_healthy_scheduled_sweep'

2 failed, 29 deselected in 0.52s
```

- `test_active_run_repairs_do_not_consume_coordinator_candidate_pages`
  received the repair-marked ordinary row in the first `limit=1` page instead
  of the healthy peer.
- `test_unrecoverable_migrated_run_does_not_block_healthy_scheduled_sweep`
  raised `JournalRecoveryError: malformed journal event at line 2` from
  `coordinator_candidates()` before any scheduler submission.

The migration regression drains all admission wakes before corrupting the
journal, creates two valid journal frames, inserts a complete malformed frame
between them, downgrades the database to a genuine v13 shape, and restarts
through v14 migration. It therefore exercises candidate-index discovery, not
wake-queue submission or torn-tail recovery.

## GREEN Implementation

`plugins/workflow/store.py` defines one correlated
`NOT EXISTS` predicate using the existing run-scoped reason set and the latest
event per `(run_id, reason_code)`. Both `coordinator_candidates()` and
`scheduled_coordinator_candidates()` add that predicate to their SQL filters.

The implementation does not:

- load, rewrite, infer, delete, or quarantine damaged evidence;
- clear or replace repair events;
- change candidate keyset ordering, bounds, exact-due range branches, or
  queue-sequence fencing;
- bypass projection/index parity validation for selected healthy rows.

## Changed Files

- `plugins/workflow/store.py`
- `tests/plugins/workflow/test_scheduled_runs.py`
- `docs/upstream-customizations/workflow-orchestration.yaml`
- `.superpowers/sdd/task-10-report.md`
- `.superpowers/sdd/progress.md`

## Verification

Focused regression GREEN:

```text
2 passed, 29 deselected in 0.5s
```

Affected store/coordinator coverage, through the canonical test wrapper:

```text
scripts/run_tests.sh \
  tests/plugins/workflow/test_scheduled_runs.py \
  tests/plugins/workflow/test_schedule_store_identity.py \
  tests/plugins/workflow/test_coordinator.py -q

102 passed in 63.7s
```

Additional checks:

- Ruff lint on both changed Python files: passed.
- Customization-ledger checker: passed.
- `git diff --check`: passed.

## Self-Review

- Active repair filtering is SQL-level, so excluded rows do not consume the
  `limit + 1` probe or affect cursor exhaustion.
- The predicate uses the latest event independently for every run-scoped
  reason. Any active reason excludes; historical `repair_required` followed
  by `repair_verified` does not.
- Scheduled selection retains the existing indexed union, exact instant
  bounds, outer keyset/fence filters, and healthy-row parity loop.
- The migration sweep test proves the healthy run is the only scheduler
  submission while the damaged row remains published, queued,
  `scheduled_at=NULL`, unquarantined, byte-preserved, and actively
  `run_evidence_uncorroborated`.

## Concerns

None. The test intentionally permits an additional
`notification_reconciliation_unverified` repair after the sweep; it asserts
that the original evidence repair remains active rather than assuming it is
the only valid run-scoped repair.

## Commit

Atomic subject: `fix(workflow): isolate repair-marked coordinator rows`
