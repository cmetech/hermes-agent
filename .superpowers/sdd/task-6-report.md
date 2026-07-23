# Task 6 Report — B-2 verified showcase cache reuse

Task base: `35fdf8f3ff68150762a19aa7a5ce33cd14408436`

## Outcome

Ordinary authenticated showcase admission now reuses the same verified,
context-free cache entry as catalog and detail reads. Admission no longer
requests a forced full verification, but every warm load still follows the
loader's live tree-signature check and generation guard before authenticated
bytes are restored into its request-owned read budget.

Admission continues to recompute compatibility and risk from the current
execution context. The loader algorithm and cache contents are unchanged.
Scheduled fire-time revalidation remains the sole production call site using
`force_reverify=True`.

## TDD evidence

Added
`test_showcase_admission_reuses_signature_checked_verified_cache`. The test
clears and warms the real showcase cache through catalog projection, captures
the entry identity and generation, wraps the real full verifier and admission
projection functions with call counters, admits the capable `ai-extensions`
showcase, then reads catalog and detail again.

Strict RED command:

```bash
scripts/run_tests.sh tests/plugins/workflow/test_runner_binding.py -q \
  -k showcase_admission_reuses_signature_checked_verified_cache
```

RED result: `1 failed, 26 deselected in 0.45s`. The assertion expected zero
additional full-verification calls but observed exactly one, proving the
admission-side `force_reverify=True` replaced the warm entry.

After removing only that admission argument, the identical command was GREEN:
`1 passed, 0 failed` in the repository wrapper's 0.6-second run. The regression
also proves identical cache-entry identity, unchanged generation, one
admission compatibility/risk projection, successful capable admission, and
runnable catalog/detail projections.

## Focused verification

- Required four-file pytest matrix:
  `.venv/bin/python -m pytest -q tests/plugins/workflow/test_runner_binding.py
  tests/plugins/workflow/test_showcase_catalog.py
  tests/plugins/workflow/test_catalog_api.py
  tests/plugins/workflow/test_desktop_api.py`
  — `260 passed in 46.27s`.
- Hermetic repository-wrapper rerun of the same four files:
  `260 passed, 0 failed` in 38.7 seconds.
- Scoped Ruff:
  `.venv/bin/python -m ruff check plugins/workflow/api_admission.py
  tests/plugins/workflow/test_runner_binding.py`
  — `All checks passed!`.
- The first customization-ledger check correctly failed because this registered
  report path did not yet exist. After creating and registering the report, the
  required validator passed with exit code 0 and no diagnostics.
- Task-base and staged diff checks passed before the atomic commit.

## Self-review

- The only production edit removes admission's forced-reverification argument.
- No loader cache algorithm, entry field, authenticated byte map, tree-signature
  check, generation guard, locking boundary, or tamper failure path changed.
- Catalog, detail, and admission still receive independent request-owned read
  budgets and recompute context-dependent compatibility and risk.
- `plugins/workflow/scheduled_revalidation.py` retains its fire-time
  `force_reverify=True` call unchanged.
- Existing tamper/restamp and generation-race coverage remains in the focused
  suite and passed.
- The customization guidance explicitly records shared admission cache reuse
  and fire-time-only forced verification.
- Untracked review-request files and `.reviews/**` were not staged or modified.
  No push, merge, tag, or release action was performed.

## Concerns

None.
