# Task 3 Report — RT-1

## Result

Preserved periodic coordinator progress under a full older due-scheduled
backlog without changing the hard 100-run budget, page limits, queries, cursor
count, or coordinator state.

The coordinator still builds its normal deduplicated deterministic order.
When that order exceeds 100 rows and the current periodic page is non-empty,
it selects the periodic page head first and fills the remaining 99 slots from
the normal order excluding that run ID. Unsaturated sweeps retain their exact
global order.

## RED evidence

Added
`test_full_due_backlog_cannot_starve_periodic_running_page` with a real
`RunStore`: 100 older scheduled rows are created first, four later immediate
background rows consume execution capacity, the scheduled clock advances to
due, and all setup wakes are completed so discovery must use the two indexed
pages. The scheduler boundary advances the injected monotonic clock by 2.1
seconds after each submission, proving the selected periodic row must be first
to beat the two-second sweep deadline.

Command:

```bash
.venv/bin/python -m pytest -q \
  tests/plugins/workflow/test_scheduled_runs.py::test_full_due_backlog_cannot_starve_periodic_running_page
```

RED result: expected `1 failed in 4.63s`. Across four bounded sweeps the
current implementation produced exactly the starvation signature:
`submitted_periodic == []` and
`observed_cursors == [None, None, None, None]`.

Added three selection-contract tests before production edits:

- exact global order is unchanged below the budget;
- saturation reserves the periodic head before the scheduled prefix while
  retaining scheduled order; and
- a periodic head already in the normal prefix appears only once.

Command:

```bash
.venv/bin/python -m pytest -q \
  tests/plugins/workflow/test_coordinator.py::test_sweep_selection_preserves_global_order_below_budget \
  tests/plugins/workflow/test_coordinator.py::test_sweep_selection_reserves_periodic_head_before_saturated_prefix \
  tests/plugins/workflow/test_coordinator.py::test_sweep_selection_does_not_duplicate_reserved_head_in_prefix
```

RED result: expected `3 failed in 0.23s`; each failed with
`AttributeError` because `_select_sweep_run_ids` did not yet exist.

## GREEN evidence

Added the private `_select_sweep_run_ids` helper and moved the existing
100-row slice into it. The ordinary merge is now built in full before the
helper applies the existing bound. The helper returns the original list
unchanged at 100 or fewer rows, preserves the old prefix when no periodic row
exists, and otherwise returns the periodic head plus 99 normal-order rows that
do not have that ID.

Command:

```bash
.venv/bin/python -m pytest -q \
  tests/plugins/workflow/test_coordinator.py::test_sweep_selection_preserves_global_order_below_budget \
  tests/plugins/workflow/test_coordinator.py::test_sweep_selection_reserves_periodic_head_before_saturated_prefix \
  tests/plugins/workflow/test_coordinator.py::test_sweep_selection_does_not_duplicate_reserved_head_in_prefix \
  tests/plugins/workflow/test_scheduled_runs.py::test_full_due_backlog_cannot_starve_periodic_running_page \
  tests/plugins/workflow/test_scheduled_runs.py::test_due_and_ordinary_candidates_share_one_global_admission_order
```

GREEN result: `5 passed in 5.89s`.

The real-store regression proves the four periodic running rows are selected
in periodic-page order, one per deadline-bounded sweep; the cursor advances
through exactly the three processed prefixes and resets only after the fourth
row; and no sweep submits a run twice.

## Files changed

- `plugins/workflow/coordinator.py`
- `tests/plugins/workflow/test_coordinator.py`
- `tests/plugins/workflow/test_scheduled_runs.py`
- `docs/upstream-customizations/workflow-orchestration.yaml`
- `.superpowers/sdd/task-3-report.md`

## Verification

- Focused suite:
  `.venv/bin/python -m pytest -q tests/plugins/workflow/test_coordinator.py
  tests/plugins/workflow/test_scheduled_runs.py` —
  initial `61 passed in 62.40s`; fresh pre-commit verification
  `61 passed in 63.10s`.
- Ruff:
  `.venv/bin/python -m ruff check plugins/workflow/coordinator.py
  tests/plugins/workflow/test_coordinator.py
  tests/plugins/workflow/test_scheduled_runs.py` —
  `All checks passed!`.
- Customization ledger verification against task base
  `521b214c962ac4ff1bcd8c12baf789e758185bfe` passed with exit code 0
  after the task report path was created and registered. The first check
  intentionally failed with `ledger path does not exist:
  .superpowers/sdd/task-3-report.md`, proving the ledger enforced the tracked
  evidence path.
- `git diff --check 521b214c962ac4ff1bcd8c12baf789e758185bfe`
  passed before the atomic commit; `git diff --check
  521b214c962ac4ff1bcd8c12baf789e758185bfe..HEAD` passed after it.

## Self-review

- The hard processing budget remains 100; the two database calls still request
  at most 100 rows each and no second scan was added.
- Selection has no state, cursor, or round-robin memory. It consumes only the
  already-fetched periodic page and normal merged order.
- The periodic page head is inserted at index zero, before the deadline check
  can terminate the loop.
- Scheduled and periodic source order remains `(created_at, run_id)`.
  Unsaturated global order is returned byte-for-byte as the same list object.
- Excluding the reserved ID from the 99-row fill prevents duplicates whether
  the periodic head was inside or outside the original prefix.
- Cursor advancement is unchanged and still stops at the first unprocessed
  periodic row, so moving the head forward cannot skip a page prefix.
- Existing uncorroborated wake handling remains in its original deterministic
  prefix; it still shares the same 100-run selection budget.
- The customization guidance now documents exact global order when the work
  fits and bounded periodic liveness only under saturation.

## Concerns

None.
