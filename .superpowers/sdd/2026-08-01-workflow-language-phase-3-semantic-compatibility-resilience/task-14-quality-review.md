# Task 14 Code Quality Review

## Review identity

- Baseline: `f4f4216651d0ab4830f232df8a4d15ee1981022e`
- Candidate: `f6dc95200fad782c4a34cd33ed97f7a17a9db698`
- Candidate tree: `018720e3d24e5eaca3dd4b1ac8d9dace00d995b9`
- Scope: Task 14 code quality and ordinary functional behavior only
- Verdict: **CHANGES REQUESTED**
- Findings: **0 Critical / 1 Important / 2 Minor**

This review inspected the complete Task 14 range, the repository and Desktop
engineering/design contracts, the Phase 3 design and Task 14 plan, the Task 14
implementation report, both specification-review reports, the changed
production and test context, and the retry/recovery producer and store shapes.
No production code or tests were modified. Per the user's restriction, no
threat-model, security-focused, exploit, adversarial, or security-boundary
validation was performed or invoked.

## Findings

### Important — Backend-authored migration guidance is transported but never displayed

**Evidence**

- The backend now attaches bounded catalog-authored `migration` text to detail
  findings (`plugins/workflow/dashboard/plugin_api.py:465-500`).
- Desktop declares the field as additive and optional
  (`apps/desktop/src/types/hermes.ts:180-186`), which is the correct compatibility
  shape for an older backend.
- The only Desktop finding renderer filters blocking findings and emits only
  `finding.message`; it never reads `finding.migration`
  (`apps/desktop/src/app/workflows/review-run-dialog.tsx:738-750`). A repository
  search finds no other workflow UI consumer of the field.
- The new v3 fixture deliberately supplies `migration: 'Backend-authored
  migration guidance'`, but the assertions check only the normalizer and
  message, so the test passes while the migration is invisible
  (`apps/desktop/src/app/workflows/review-run-dialog.test.tsx:311-342`).
- This is not merely transport-only data: the approved Desktop contract says
  compatibility blockers and migration guidance come from the backend, and
  the Task 14 rendering step requires the new renderer to display
  backend-authored findings
  (`docs/superpowers/specs/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience-design.md:913-926`;
  `docs/superpowers/plans/2026-08-01-workflow-language-phase-3-semantic-compatibility-resilience.md:930-938`).

**Impact**

A user sees that a workflow is incompatible but loses the exact actionable
migration instruction the backend added for that finding. The implementation
therefore pays the API/type cost for the new guidance without delivering it at
the only Desktop compatibility surface. The current test fixture masks the
regression by never asserting the supplied field.

**Recommendation**

Render `finding.migration` adjacent to its finding when it is present, while
preserving the existing message-only presentation when an older backend omits
it. Extend the v3 test to assert the migration text and add/retain a missing-field
case proving older details remain usable.

### Minor — Recovery formatting unnecessarily changes every non-log inspector tab

**Evidence**

- `formatEvidenceItem()` replaces the former pretty JSON representation with
  sorted `key=value` lines (`apps/desktop/src/app/workflows/run-inspector.tsx:45-51`).
- That formatter is applied to every non-log `EvidenceItems` call, including
  timeline, attempts, outputs, cleanup, coordinator, notifications, recovery,
  and legacy artifact fallback (`apps/desktop/src/app/workflows/run-inspector.tsx:54-75,190-197`).
- Task 14 only needs the existing recovery tab to render generic persistent-
  session evidence. The added test checks two recovery substrings, while the
  existing attempt test checks only that an ID remains somewhere in the text;
  there is no regression assertion for nested outputs, timeline records, or
  other established evidence tabs
  (`apps/desktop/src/app/workflows/index.test.tsx:978-990,1004-1029`).

**Impact**

Nested evidence is now minified inside a single value, and raw multiline string
values lose JSON quoting/escaping, so continuation lines can look like separate
fields. This is a user-visible readability change across unrelated evidence
surfaces even though their data contract did not change.

**Recommendation**

Keep the established pretty JSON formatter for existing tabs and apply any
recovery-specific presentation only when it is actually needed, or introduce a
per-kind formatter with explicit behavior tests for attempts, outputs, timeline,
and recovery.

### Minor — The new evidence union is swallowed by its generic member

**Evidence**

- `WorkflowAttemptEvidence` and
  `WorkflowPersistentSessionRecoveryEvidence` define useful field contracts
  (`apps/desktop/src/types/hermes.ts:341-369`).
- `WorkflowEvidenceItem` then unions both with `Record<string, unknown>`
  (`apps/desktop/src/types/hermes.ts:371-374`). Because each specific interface
  already extends and is assignable to that broad record, the generic member
  accepts every specific value and prevents the union from providing a useful
  discriminant or narrowing contract.
- The renderer consequently still accepts only
  `Array<Record<string, unknown>>` and consumes none of the new interfaces
  (`apps/desktop/src/app/workflows/run-inspector.tsx:54-61`). A repository search
  finds no production consumer of either specific interface.

**Impact**

The type additions suggest that attempt and persistent-session evidence are
modeled, but consumers receive no compile-time help distinguishing or safely
reading those shapes. Future UI work can still mistype field names or assume a
shape without narrowing, and TypeScript will not catch it.

**Recommendation**

Either keep the page explicitly generic and add real type guards for the known
shapes, or model evidence by kind with a discriminated/generic response mapping
whose fallback does not subsume the known members. Then have `RunInspector`
consume the resulting type rather than immediately widening back to `Record`.

## Positive observations

- The Phase 3 attempt projection is a closed allowlist and now retains the
  producer-authored `additional_provider_attempts`; the real scheduler-to-route
  test checks the exact response shape.
- The persistent-session recovery projection matches the complete durable
  producer record and omits unrelated internal fields. The store's recovery
  record construction supplies every required public field, while the API
  projector remains tolerant of legacy/partial stored mappings.
- `migration` is optional in the Desktop finding interface, so older backend
  details do not become type-invalid merely because the additive field is
  absent.
- No new endpoint, renderer-side workflow authority, parser, calculator,
  session probe, or filesystem dependency was introduced.

## Summary

| Severity | Count |
|---|---:|
| Critical | 0 |
| Important | 1 |
| Minor | 2 |

Task 14's backend retry/recovery projection is technically sound, but the
Desktop portion should not be approved until the already-projected migration
guidance is actually visible. The formatter and type-union findings are smaller
quality issues that should be addressed in the same focused closure pass if
practical.
