# Task 11 Report — Ignore the Local SDD Progress Ledger

## Outcome

Customization coverage now ignores exactly
`.superpowers/sdd/progress.md`, including historical add and delete records
visited in commit-scoped coverage mode. Adjacent `.superpowers/sdd/` files
remain subject to ordinary ledger coverage.

The two mixed production commits that accidentally tracked and then untracked
the ledger remain in the coverage history. The local progress file remains
ignored, untracked, and byte-preserved.

## Strict RED Evidence

The regression was added before the checker changed. It created a
coverage-scoped repository history that tracked and deleted
`.superpowers/sdd/progress.md`, then required an adjacent unregistered SDD file
to remain rejected.

```text
scripts/run_tests.sh tests/scripts/test_check_upstream_customizations.py -q \
  -k ignores_only_local_sdd_progress_ledger

1 failed, 8 deselected
ValueError: upstream-owned files missing from customization ledger:
.superpowers/sdd/progress.md
```

The failure named the confirmed historical-path bug and occurred before the
adjacent-file assertion.

## GREEN Implementation

`_EPHEMERAL_COVERAGE_PATHS` contains one exact repository-relative path:
`.superpowers/sdd/progress.md`. `validate_diff_coverage()` checks exact
membership before its existing prefix and additive-root classification.
No `.superpowers/sdd/` prefix exemption was added.

The same focused command passed:

```text
1 passed, 8 deselected
```

The passing test also proves that
`.superpowers/sdd/unregistered.md` still raises the normal missing-coverage
error.

## Changed Files

- `scripts/check_upstream_customizations.py`
- `tests/scripts/test_check_upstream_customizations.py`
- `docs/upstream-customizations/workflow-orchestration.yaml`
- `.superpowers/sdd/task-11-report.md`
- `.superpowers/sdd/progress.md` (ignored local ledger update only)

## Verification

- Focused regression through `scripts/run_tests.sh`: 1 passed.
- Complete `test_check_upstream_customizations.py`: 9 passed.
- Ruff lint on the changed checker and test: passed.
- Live customization checker with `--diff HEAD`: passed.
- `git diff --check`: passed.
- Neither `532c0274c` nor `8865c7bf3` was added to
  `coverage.excluded_commits`.
- `.superpowers/sdd/progress.md` remains ignored and untracked; its SHA-256
  before the final local ledger update was
  `2cb66c55049fa091e95f5a13f78269d0eccaed6456c87c9b022002f30c77c0ec`.

## Self-Review and Concerns

The production change is a two-line exact-path classification boundary. It
does not affect manifest validation, ordinary diff mode, commit enumeration,
expected-subject enforcement, or any Workflow runtime behavior.

An exploratory `ruff format --check` reported broad pre-existing formatting
drift in both legacy files. Ruff lint is clean; no unrelated bulk formatting
was applied.

No functional concerns remain.
