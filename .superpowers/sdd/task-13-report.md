# Task 13 Report — Restore Recoverable Repair Projections

## Outcome

FV-RT-A-1 is closed for both recovery paths. After a v13→v14 migration clears
the derived SQLite schedule for a published run whose `run.json` no longer
corroborates its intact journal, unattended coordinator revalidation now
replays the captured journal, atomically adopts its authoritative projection,
restores the exact canonical `scheduled_at`, records `repair_verified`, and
allows the run to submit on a later sweep. A direct `load_run()` first touch
now resynchronizes that derived schedule before integrity parity and returns
the rebuilt projection instead of raising.

## Strict RED Evidence

The real migration/coordinator regression failed against unchanged production
code:

```text
scripts/run_tests.sh tests/plugins/workflow/test_scheduled_runs.py \
  -k v13_migration_revalidates_rewritten_projection_before_later_submission -q

0 passed, 1 failed, 44 deselected
```

The healthy due run submitted on the first sweep, but after four later sweeps
the damaged run still had not submitted; only the healthy run appeared in the
scheduler calls. This proved unattended revalidation never adopted the
journal projection.

The direct-load regression independently failed against unchanged production
code:

```text
scripts/run_tests.sh tests/plugins/workflow/test_schedule_store_identity.py \
  -k first_load_after_v13_repair_rebuild_resynchronizes_schedule -q

0 passed, 1 failed, 33 deselected
```

`load_run()` raised `JournalRecoveryError: run schedule index parity mismatch`
from `_sync_integrity_index()` after it had rebuilt `run.json`, proving the
migration-cleared derived column was not resynchronized first.

Self-review extended the coordinator regression across non-JSON projection
bytes and syntactically valid wrong-run projection JSON. Those two incremental
TDD cycles failed at 1 passed/1 failed and 2 passed/1 failed respectively
before their fallback boundary was corrected.

## Implementation

- Added a repair-only projection adoption path that is reached only after
  paired captured projection/journal corroboration fails.
- The captured projection bytes are never trusted for recovery. The captured,
  quota-bounded journal bytes are replayed as the authority, retaining journal
  frame, run-identity, sequence, digest, and projection validation.
- Before replacing `run.json`, bounded rereads must exactly equal both captured
  projection and normalized journal snapshots. Replacement, growth,
  disappearance, or mismatch fails closed without recording
  `repair_verified`.
- The rebuilt serialized projection, captured journal, and captured legacy
  policy remain bounded by `max_run_bytes`; captured journal bytes remain the
  bytes used for replay and hashing.
- Projection adoption uses the existing atomic durable byte replacement while
  the existing per-run lock is held. Torn-tail normalization and its own
  compare-before-write rules are unchanged.
- Direct-load reconstruction derives the canonical schedule from the rebuilt
  projection before writing and updates the SQLite `scheduled_at` column
  before the existing integrity sync. Already-corroborated load parity is
  unchanged.
- The five pre-existing no-snapshot callers of
  `_corroborate_run_evidence_locked()` remain unchanged.

## Changed Files

- `plugins/workflow/store.py`
- `tests/plugins/workflow/test_scheduled_runs.py`
- `tests/plugins/workflow/test_schedule_store_identity.py`
- `docs/upstream-customizations/workflow-orchestration.yaml`
- `.superpowers/sdd/task-13-report.md`

The ignored `.superpowers/sdd/progress.md` is updated separately and remains
untracked. Protected review requests, `.reviews/**`, `brands/**`, and
`plugins/model-providers/**` were not modified.

## GREEN Verification

Focused regressions:

```text
coordinator projection-damage matrix: 3 passed
direct-load first-touch regression: 1 passed
```

Required six-file recovery matrix:

```text
scripts/run_tests.sh \
  tests/plugins/workflow/test_schedule_store_identity.py \
  tests/plugins/workflow/test_scheduled_runs.py \
  tests/plugins/workflow/test_coordinator.py \
  tests/plugins/workflow/test_schema_migrations.py \
  tests/plugins/workflow/test_fault_injection.py \
  tests/plugins/workflow/test_notifications.py -q

152 passed, 0 failed
```

The task brief names `test_migrations.py`; that file does not exist in this
checkout, so the repository's migration suite
`test_schema_migrations.py` was used.

Additional checks:

- Scoped Ruff lint on the changed Python/test files: passed.
- `git diff --check` on the task range: passed.
- Protected `brands/` and `plugins/model-providers/` range diff: empty.
- Live customization-ledger checker: passed.
- Pre-commit range name/status and stat checks show only the four tracked
  implementation/manifest/test surfaces; the ignored report will be
  force-added as the fifth atomic artifact.
- Staged name/status lists exactly those five artifacts; staged diff check
  passed and protected review/brand/provider staged diffs are empty.
- Post-commit range checks: pending the atomic commit.

## Self-Review

- Healthy-first behavior is explicit: only the healthy due run submits on the
  first sweep; a repaired run can submit only on a later sweep.
- The coordinator regression performs no out-of-band `load_run()` between
  migration and unattended repair.
- Valid rewritten, non-JSON, and wrong-run projection bytes all converge from
  the intact journal. Journal corruption and journal run-identity mismatch
  still fail during replay and cannot become `repair_verified`.
- Exact bounded rereads occur after journal replay and immediately before
  atomic projection adoption, preserving the immutable-snapshot and TOCTOU
  defenses.
- Existing quota, torn-tail, lock-budget, cursor fairness/wrap/reset,
  candidate exclusion, direct-load recovery, and schedule parity coverage are
  included in the green matrix.
- Unscheduled/non-AI start digest logic, scheduler/coordinator APIs, MCP
  behavior, REST values, consent/token machinery, and core tool schemas are
  untouched.

## Concerns

None.

## Commit

Planned atomic subject:
`fix(workflow): restore recoverable repair projections`
