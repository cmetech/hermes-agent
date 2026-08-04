# Task 14 Final Specification Confirmation

## Review identity

- Baseline: `f4f4216651d0ab4830f232df8a4d15ee1981022e`
- Prior approved spec candidate: `f6dc95200fad782c4a34cd33ed97f7a17a9db698`
- Final candidate: `b8be4a3182eb9a2c03834a32aecf8beb4c531df3`
- Final candidate tree: `95f1ab3eaf6e8f00a479755a5d16e218880896aa`
- Verdict: **APPROVED**
- Findings: **0 Critical / 0 Important / 0 Minor**

This was a read-only final Task 14 specification confirmation after the
quality-closure commit. No production code or tests were modified. Per the
user's restriction, no threat-model, security-focused, security-boundary,
exploit, or adversarial validation was performed or invoked.

## Quality-closure disposition

### Backend-authored migration guidance now renders

The Review & Run dialog renders `finding.migration` only when supplied by the
backend, adjacent to the backend-authored finding message
(`apps/desktop/src/app/workflows/review-run-dialog.tsx:738-755`). The v3 test
asserts both values render from the same finding
(`apps/desktop/src/app/workflows/review-run-dialog.test.tsx:311-345`). No local
migration table or inference was added.

### Generic recovery rendering remains additive

Run Inspector still requests the existing `kind=recovery` evidence page and
renders the returned record generically as JSON. The partial older-compatible
recovery fixture—containing only attempt, outcome, and recovery kind—still
renders successfully
(`apps/desktop/src/app/workflows/index.test.tsx:1022-1048`). Complete v3
persistent-session records may be narrowed for stable React keys, but the
guard does not alter, compute, or replace backend values
(`apps/desktop/src/app/workflows/run-inspector.tsx:84-113`).

### Older evidence shapes remain usable

`WorkflowEvidencePage.items` is deliberately an array of generic records
(`apps/desktop/src/types/hermes.ts:333-339`). The complete-shape guards return
false for partial legacy/older records, which continue through the generic
JSON renderer instead of being rejected. Tests cover both an incomplete older
attempt and an incomplete persistent-session record
(`apps/desktop/src/app/workflows/index.test.tsx:995-1008,1022-1048,1050-1097`).

### Prior retry-evidence closure remains intact and closed

The quality commit does not modify the Python projection. Its exact allowlist
still retains `additional_provider_attempts` with the approved
requested/effective fields and excludes `provider_attempts_exact` and unrelated
metadata (`plugins/workflow/evidence.py:33-40,385-406`). The real Archon v3
scheduler-to-authenticated-route assertion remains exact
(`tests/plugins/workflow/test_workflow_language_desktop_e2e.py:375-395`), and
the Desktop complete type/guard includes the same required field
(`apps/desktop/src/types/hermes.ts:341-355`;
`apps/desktop/src/app/workflows/run-inspector.tsx:58-82`). Legacy records still
use the unchanged generic fallback unless every Phase 3 retry field is present.

## Complete Task 14 boundary recheck

| Requirement | Result |
|---|---|
| Additive normalizer v3 language status | PASS |
| Bounded backend-authored compatibility and migration guidance | PASS |
| Closed requested/effective retry and error projection | PASS |
| Closed persistent-session recovery projection retaining sanctioned digests | PASS |
| Existing authenticated routes and publication-ID artifact lookup only | PASS |
| Versioned durable-code authority, no duplicate migration authority | PASS |
| Desktop generic recovery rendering | PASS |
| Older/newer Desktop compatibility | PASS |
| No renderer-side parser, retry calculator, session probe, filesystem access, or alternate workflow authority | PASS |

The quality closure changes only Desktop presentation/types/tests and the Task
14 report. It adds no backend route, endpoint parameter, persistence behavior,
or evidence field.

## Independent focused verification

```bash
cd apps/desktop
npm test -- \
  src/app/workflows/index.test.tsx \
  src/app/workflows/review-run-dialog.test.tsx \
  src/app/workflows/view-workflow-dialog.test.tsx
```

Result: **114 passed / 0 failed** across three files.

```bash
HERMES_TEST_FILE_RETRIES=0 scripts/run_tests.sh \
  tests/plugins/workflow/test_evidence_api.py \
  tests/plugins/workflow/test_workflow_language_desktop_e2e.py
```

Result: **32 passed / 0 failed** across two files, with no flaky retry.

`git diff --check f6dc95200..b8be4a318` was clean. The feature worktree was
clean apart from ignored retained review reports.

## Final disposition

The quality-closure commit preserves every approved Task 14 requirement, and
the prior retry-evidence specification finding remains fully closed. There are
no remaining Task 14 specification findings at the final candidate identity.
