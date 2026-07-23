# Task 5 Remediation Report — RT-3 migration fault isolation

Task base: `9acd5e511fd44af1257a883d6073de29ac401a6c`

Status: implementation and self-review complete.

## Outcome

The schema 13 to 14 scheduled-query backfill now leaves an uncorroborated
published row indexed with `scheduled_at=NULL` instead of converting that
single-run evidence fault into a database error. The existing ordinary
admission reconciliation path then records only the run-scoped
`run_evidence_uncorroborated`/`repair_required` transition.

Corroborated published and reserved rows are still backfilled from
`run_metadata.schedule_at`, which remains the sole schedule authority.
Incomplete reserved rows retain their existing reconciliation behavior. The
fix does not guess a schedule, delete or quarantine run evidence, mark global
repair from the schema transaction, replace the admission database, or rotate
its generation. Healthy derived schedules and unrelated admission remain
usable.

## TDD evidence

The production file was unchanged when the regression
`test_v13_migration_scopes_uncorroborated_published_evidence_to_one_run` was
added. The test publishes two scheduled runs, downgrades the live database to
schema 13 without `scheduled_at`, makes only one final projection disagree
with its journal, and reopens the store.

Strict RED command:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_schedule_store_identity.py \
  -q -k v13_migration_scopes_uncorroborated_published_evidence_to_one_run
```

RED result: `1 failed, 31 deselected in 0.19s`. The first contract failure was
the generation invariant: the original generation
`3b23c0a82181412da57424bca2cfe97b` was replaced by
`172d2a87d94748d88220658708fd4017`, proving the current migration preserved
and recreated the whole index instead of isolating the damaged run.

After the minimal migration exception-path change, the identical command was
GREEN: `1 passed, 0 failed` in the repository wrapper's 0.4-second run.

The complete scheduled-store identity file was then GREEN:
`32 passed, 0 failed` in 1.3 seconds. This includes the existing durable
reserved backfill, incomplete reserved cleanup, namespace migration,
projection/index parity, and reconstruction contracts.

## Focused verification

- Required four-file pytest matrix:
  `79 passed in 5.89s`.
- Hermetic repository-wrapper rerun of the same four files:
  `79 passed, 0 failed` in 3.2 seconds.
- Scoped Ruff check:
  `All checks passed!`.
- Customization ledger against task base:
  passed after this registered report path was created.
- Task-base diff checks:
  passed before and after the atomic commit.

## Self-review

- The sole production change is the published-row failure branch in
  `_migrate_scheduled_at`; successful corroboration and backfill are unchanged.
- The damaged row keeps its SQL identity and a null derived schedule until
  ordinary reconciliation marks the run-local repair.
- The regression proves schema 14, SQLite integrity, stable generation, no
  `admission-index-*` quarantine, exact healthy schedule derivation, preserved
  damaged evidence, healthy global storage, and successful unrelated
  admission.
- No run metadata authority, namespace migration, reserved-row handling,
  evidence retention, repair taxonomy, admission lock ordering, or tool surface
  changed.
- Untracked review-request files and `.reviews/**` were not staged or modified.
  No push, merge, tag, or release action was performed.

## Concerns

None.
