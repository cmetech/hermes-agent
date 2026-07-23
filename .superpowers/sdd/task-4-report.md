# Task 4 Report — RT-4 trust-store bound parity

Base HEAD: `9e3c5baab8556e8f0b58d8ad94425041c0d4d78f`

## Outcome

Scheduled fire-time revalidation now imports and uses the canonical
`catalog_api.CATALOG_MAX_TRUST_STORE_BYTES` (4 MiB) for its sole read-only
trust-store snapshot. The obsolete local 1 MiB ceiling is removed. Admission
and fire-time trust-store bounds therefore remain aligned without changing
the trust error taxonomy, lock/TOCTOU boundaries, or trust classification.

## TDD evidence

Added `test_scheduled_user_with_catalog_sized_trust_store_promotes`, using the
real scheduled admission and scheduler promotion path. It trusts a profile
workflow, expands the still-valid JSON trust store to 1,048,576 bytes plus
JSON framing (above 1 MiB and below 4 MiB), retains the target record, removes
the admission-time lock fixture, and advances at the unchanged due binding.

RED command:

```bash
.venv/bin/python -m pytest -q \
  tests/plugins/workflow/test_schedule_revalidation.py::test_scheduled_user_with_catalog_sized_trust_store_promotes
```

RED result: `1 failed in 0.27s`; the current 1 MiB fire-time snapshot reduced
the valid oversized snapshot to untrusted, so the scheduled result was
`failed` rather than `succeeded`.

GREEN result after substituting the canonical 4 MiB ceiling: `1 passed in
0.29s`. The promoted run succeeds and the assertion confirms revalidation did
not create a trust lock file.

## Verification

- Focused workflow suite:
  `.venv/bin/python -m pytest -q tests/plugins/workflow/test_schedule_revalidation.py tests/plugins/workflow/test_catalog_api.py tests/plugins/workflow/test_trust_policy.py`
  — post-commit `127 passed in 9.46s`.
- Scoped Ruff:
  `.venv/bin/python -m ruff check plugins/workflow/scheduled_revalidation.py tests/plugins/workflow/test_schedule_revalidation.py`
  — `All checks passed!`.
- Customization ledger:
  `.venv/bin/python scripts/check_upstream_customizations.py --manifest docs/upstream-customizations/workflow-orchestration.yaml --diff 9e3c5baab8556e8f0b58d8ad94425041c0d4d78f`
  — post-commit passed with exit code 0 (`2m28.35s`).
- `git diff --check 9e3c5baab8556e8f0b58d8ad94425041c0d4d78f..HEAD`
  and the working-tree diff check both passed.

## Self-review

- Fire-time revalidation still takes exactly one bounded read-only snapshot.
- No trust-store write, lock creation, error remapping, or TOCTOU boundary was
  added.
- The regression retains the trusted target record while exercising a payload
  accepted by catalog admission.
- The customization ledger now explicitly documents admission/fire-time
  trust-bound parity and registers this evidence file.

## Concerns

None.
