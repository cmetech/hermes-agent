# Task 1 Report — RT-2

## Result

Deferred the scheduled forbid-overlap failure notification until the existing
journal reconciliation path projects it after the promotion transaction.

## RED evidence

Added
`test_forbid_overlap_defers_outbox_until_after_promotion_transaction` beside
the declared-overlap-policy coverage. The test replaces the call-time imported
`NotificationOutbox` with a no-SQLite fake only during `try_promote_run`; its
`record()` raises immediately if promotion attempts an in-transaction outbox
write.

Command:

```bash
.venv/bin/python -m pytest -q tests/plugins/workflow/test_scheduled_runs.py \
  -k forbid_overlap_defers_outbox_until_after_promotion_transaction
```

RED result: expected failure. `RunStore._append_locked()` reached
`NotificationOutbox(self).record()` at `plugins/workflow/store.py:6994`; the
fake raised `AssertionError: outbox record called during promotion transaction`.

## GREEN evidence

Added `defer_notification=True` only to the
`schedule_overlap_forbidden` `run_failed` `_append_locked` call. The terminal
projection, journal transition, integrity update, coordinator wake, and commit
remain in the existing promotion transaction. After restoring the real outbox,
the test calls `reconcile_journal()` and verifies one failure fact and one
outbox row, then verifies a second reconciliation creates no duplicate.

The same targeted command passed: `1 passed, 24 deselected in 0.20s`.

## Files changed

- `plugins/workflow/store.py`
- `tests/plugins/workflow/test_scheduled_runs.py`
- `docs/upstream-customizations/workflow-orchestration.yaml`
- `.superpowers/sdd/task-1-report.md`

## Verification

- Scheduled-run focused suite, split to stay within the terminal command time
  limit: all 25 tests passed (batches: 9, 9, 1, 1, 1, 2, 1, 1).
- `.venv/bin/python -m pytest -q tests/plugins/workflow/test_notifications.py`
  — `12 passed in 2.09s`.
- `.venv/bin/python -m ruff check plugins/workflow/store.py
  tests/plugins/workflow/test_scheduled_runs.py` — `All checks passed!`.
- `.venv/bin/python scripts/check_upstream_customizations.py --manifest
  docs/upstream-customizations/workflow-orchestration.yaml --diff
  e6155f2060e5049a4dd8213da5bf726cfe1c48e5` — passed.
- `git diff --check` — passed before commit; `git diff --check
  e6155f2060e5049a4dd8213da5bf726cfe1c48e5..HEAD` passed after commit.

## Self-review

- The production change is a one-argument deferral at the exact nested outbox
  site; no alternate post-commit writer was added.
- The test verifies durable terminal evidence (`failed`, reason, one
  `run_failed`, and no worker claim) before exercising ordinary journal repair.
- The fake is installed only while promotion runs and restores the real outbox
  before reconciliation, making the regression independent of SQLite lock
  timing.
- The manifest states the defer-and-journal-repair contract on the existing
  scheduled queued-consumer customization entry.

## Concerns

None. The initial direct `record` patch did not fail because the real outbox
constructor attempts its own SQLite work under the held promotion transaction
and that SQLite error is swallowed before `record`; the no-SQLite fake makes
the actual forbidden call deterministic and provides genuine RED evidence.
