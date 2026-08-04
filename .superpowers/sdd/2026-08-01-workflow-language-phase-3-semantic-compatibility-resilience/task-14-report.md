# Task 14 Report — Bounded Phase 3 API and Desktop Evidence

## Outcome

Workflow detail responses now accept normalizer v3 and add bounded migration
guidance sourced from the shared versioned compatibility-code catalog. Catalog
summaries keep their existing smaller shape, so the API does not create a
second compatibility authority.

Phase 3 attempt evidence uses explicit requested/effective retry fields and a
separate bounded error object. Persistent-session recovery evidence uses
`recovery_kind: persistent_session` and a closed projection containing only
the durable recovery outcome, bounded provider/profile metadata, and the two
corroborating SHA-256 digests. Raw session identifiers, cache fingerprints,
registry keys, pending obligations, histories, and provider payloads are not
projected.

Desktop types accept the additive v3 fields. The existing Run Inspector renders
generic bounded recovery evidence without adding a workflow parser, retry
calculator, session probe, filesystem access, endpoint, or alternate workflow
authority. Older backend shapes remain usable when the additive fields are
absent.

## TDD Evidence

- Backend RED: the exact initial API projection command reported 139 passed / 3
  failed because attempt/recovery evidence and detail migration projection did
  not yet exist.
- Durable-code authority RED: 18 passed / 1 failed because the API migration
  resolver did not yet derive from `compatibility_code_catalog()`.
- Desktop RED: 111 passed / 2 failed because generic recovery evidence was not
  rendered in the requested form and the v3 fixture did not yet expose its
  backend-authored blocking finding.
- GREEN: the required six-file backend matrix reported 321 passed / 0 failed;
  the required three-file Desktop matrix reported 113 passed / 0 failed.

All Python test invocations used `scripts/run_tests.sh` with
`HERMES_TEST_FILE_RETRIES=0`. No threat-model, adversarial, or other
security-focused validation suite was invoked.

## Verification

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_phase3_code_catalog.py \
  tests/plugins/workflow/test_catalog_api.py \
  tests/plugins/workflow/test_workflow_detail_api.py \
  tests/plugins/workflow/test_evidence_api.py \
  tests/plugins/workflow/test_workflow_language_desktop_e2e.py \
  tests/plugins/workflow/test_desktop_api.py
```

Result: **321 passed / 0 failed** across six files.

```bash
cd apps/desktop
npm test -- src/app/workflows/index.test.tsx \
  src/app/workflows/review-run-dialog.test.tsx \
  src/app/workflows/view-workflow-dialog.test.tsx
```

Result: **113 passed / 0 failed** across three files.

`npm run typecheck`, scoped Ruff, scoped ESLint, scoped Prettier, and
`git diff --check` all completed with zero errors. Scoped ESLint retained only
23 pre-existing test-file warnings.

## Files Changed

- `plugins/workflow/evidence.py`
- `plugins/workflow/dashboard/plugin_api.py`
- `apps/desktop/src/types/hermes.ts`
- `apps/desktop/src/app/workflows/run-inspector.tsx`
- `apps/desktop/src/app/workflows/index.test.tsx`
- `apps/desktop/src/app/workflows/review-run-dialog.test.tsx`
- `apps/desktop/src/app/workflows/view-workflow-dialog.test.tsx`
- `tests/plugins/workflow/test_evidence_api.py`
- `tests/plugins/workflow/test_phase3_code_catalog.py`
- `tests/plugins/workflow/test_workflow_detail_api.py`

## Concerns

None blocking. Legacy attempt evidence intentionally retains its established
shape; the closed retry/error projection activates only when the complete Phase
3 retry metadata contract is present.

## Specification Review Fix

The closed Phase 3 retry projection now retains the producer-authored
`additional_provider_attempts` integer, and the Desktop retry interface exposes
the same required field. `provider_attempts_exact` and unrelated attempt
metadata remain outside the approved evidence shape.

The focused RED command was:

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_evidence_api.py \
  tests/plugins/workflow/test_workflow_language_desktop_e2e.py
```

RED: **30 passed / 2 failed**. The exact synthetic projection and a real Archon
v3 scheduler-to-authenticated-evidence-route assertion both failed because the
field was absent. After the allowlist fix, the same command was GREEN:
**32 passed / 0 failed**.
