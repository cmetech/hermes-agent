# Workflow Language Phase 4 adversarial remediation

Date: 2026-08-06

Source review:
`docs/reviews/2026-08-06-workflow-language-phase-4-adversarial-review-fable.md`

## Disposition

All eight findings were independently reproduced and confirmed. F-1 through F-8
are resolved by this remediation. The suggested F-1 loader-wide rejection was not
used because unquoted YAML dates, binary values, and non-finite floats are accepted
by v1-v3; v4 now preserves those values while encoding them deterministically.

| Finding | Resolution |
|---|---|
| F-1 | v4 compilation preserves accepted YAML-native scalars, uses collision-safe canonical tags, contains compiler `TypeError` per catalog entry, and translates scheduled revalidation failures into its bounded error taxonomy. |
| F-2 | REST mutation validation reads the raw node interaction instead of the public projection annotated with `node_id`; real-router approve and provide-input mutations now succeed and stale versions return `stale_state`. |
| F-3 | Interrupted/cancelled loop completion persists clean committed state, and a valid decision bound to a known terminal prior attempt is strictly superseded under a known live replacement attempt. |
| F-4 | A failed feedback-consuming iteration retains the last committed output and iteration, clears consumed feedback exactly once, and resumes at the next uncommitted iteration. |
| F-5 | Primary and repaired notification writes share the same node-level pending-interaction projection; repair payload parity is asserted field-for-field. |
| F-6 | v4 doctor inventories use logical `source:name::relative/path` dependency bindings rather than sealed snapshot paths. |
| F-7 | showcase entitlement and trusted capability staging use the v4 composite compilation identity while legacy packages retain root-package identity. |
| F-8 | validation evidence now reports the observed 32,338/28 full run, the later 517/517 bounded fix, and the absence of a second full run without deriving an impossible 32,338/27 result. |

The installed-distribution integration test was also separated from mixed commands
that inherit the repository's default `not integration` marker selection.

## Verification

Every Python test command used `scripts/run_tests.sh` with file retries disabled.

- Complete focused Phase 4 gate: 232 passed, 0 failed.
- Surrounding recovery, catalog, scheduled revalidation, Desktop API, notification,
  doctor, entitlement, capability staging, v1-v3, and snapshot gate: 584 passed,
  0 failed.
- Installed-distribution wheel test with `-m integration`: 1 passed, 0 failed.
- Complete workflow-plugin suite: 4,953 passed, 6 failed. The six failures are the
  same exact-base cases documented by the Phase 4 validation record: five packaged
  schema fixture-mutation assertions and one journal-order error-message assertion.
  No remediation-only failure remained.
- Ruff on every touched Python file: passed.
- `git diff --check`: passed.

An independent Sol review converged with 0 Critical, 0 Important, and 0 Minor
findings. It additionally exercised a v1-v3 native-scalar and snapshot matrix and
found no compatibility regression.
