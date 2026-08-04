# Task 14 Code Quality Rereview

## Review identity

- Baseline: `f4f4216651d0ab4830f232df8a4d15ee1981022e`
- Prior candidate: `f6dc95200fad782c4a34cd33ed97f7a17a9db698`
- Closure candidate: `b8be4a3182eb9a2c03834a32aecf8beb4c531df3`
- Closure tree: `95f1ab3eaf6e8f00a479755a5d16e218880896aa`
- Scope: Task 14 code quality and ordinary functional behavior only
- Verdict: **APPROVED**
- Findings: **0 Critical / 0 Important / 0 Minor**

This rereview checked the complete Task 14 range and the focused closure diff,
then re-traced the three findings from `task-14-quality-review.md` through the
final production and test call sites. No production code or tests were modified.
Per the user's restriction, no threat-model, security-focused, exploit,
adversarial, or security-boundary validation was performed or invoked.

## Prior finding closure

### Important — Backend-authored migration guidance is transported but never displayed

**Closed.** The only Desktop compatibility-finding renderer now places the
optional backend-authored migration directly after its associated blocking
message in the same list item
(`apps/desktop/src/app/workflows/review-run-dialog.tsx:738-754`). The conditional
uses the additive optional field directly; it introduces no local migration
calculation or duplicate authority.

The v3 test now locates the message's list item and asserts the migration is
present inside that exact item
(`apps/desktop/src/app/workflows/review-run-dialog.test.tsx:311-345`). Existing
message-only findings remain covered by the older-backend/non-v3 tests, and the
conditional omits the second paragraph when `migration` is absent
(`apps/desktop/src/app/workflows/review-run-dialog.test.tsx:1058-1088`). Thus an
older backend retains the established usable presentation.

### Minor — Recovery formatting unnecessarily changes every non-log inspector tab

**Closed.** `EvidenceItems` again uses the pre-Task-14 indented
`JSON.stringify(item, null, 2)` representation for every non-log item; only the
existing raw-log text path remains special
(`apps/desktop/src/app/workflows/run-inspector.tsx:115-141`). Timeline and every
evidence tab still share this single generic component
(`apps/desktop/src/app/workflows/run-inspector.tsx:252-258`), so no tab-specific
formatting branch or second renderer was introduced.

The attempt regression now asserts the complete indented nested object and
proves an embedded newline stays JSON-quoted and escaped
(`apps/desktop/src/app/workflows/index.test.tsx:974-1008`). The recovery test
asserts the same generic JSON representation still exposes the persistent-
session fields (`apps/desktop/src/app/workflows/index.test.tsx:1022-1048`).

### Minor — The new evidence union is swallowed by its generic member

**Closed.** `WorkflowEvidencePage.items` is explicitly the honest legacy/additive
boundary, `Array<Record<string, unknown>>`; the subsuming union was removed
(`apps/desktop/src/types/hermes.ts:333-339`). The known attempt and recovery
interfaces now describe complete closed projections rather than optional partial
records (`apps/desktop/src/types/hermes.ts:341-369`).

Two pure runtime guards validate every required field before narrowing
(`apps/desktop/src/app/workflows/run-inspector.tsx:50-101`). They are not test-only
types: `evidenceItemKey()` uses the guards in production to build node/attempt
stable identities for complete attempt and recovery records, while partial and
legacy records retain the generic fallback
(`apps/desktop/src/app/workflows/run-inspector.tsx:103-113,130-135`). The tests
exercise successful narrowing and incomplete-record rejection for both shapes
(`apps/desktop/src/app/workflows/index.test.tsx:1050-1097`).

## Full-range quality disposition

- The backend attempt allowlist still retains the exact requested/effective
  retry contract, including `additional_provider_attempts`, without widening to
  unrelated metadata.
- Persistent-session recovery still projects the complete producer-authored
  public record through a closed field set, while legacy/partial mappings remain
  tolerated as generic evidence.
- Migration remains backend-authored and optional, preserving older-backend
  compatibility.
- The closure added no endpoint, workflow parser/calculator, session probe,
  filesystem dependency, state authority, navigation, focus behavior, or new
  user-facing localized copy.
- The pure guards perform finite-number and complete-shape checks before typed
  access; malformed or incomplete additive records safely fall back to generic
  display and index-based/existing identity behavior.
- The closure diff is focused on the three review findings and introduces no
  unrelated production behavior change.

No new quality, UX, accessibility, performance, maintainability, data-shape, or
test-fidelity finding remains in the Task 14 range.

## Independent ordinary functional verification

```bash
cd apps/desktop
npm test -- src/app/workflows/index.test.tsx \
  src/app/workflows/review-run-dialog.test.tsx \
  src/app/workflows/view-workflow-dialog.test.tsx
```

Result: **114 passed / 0 failed** across three files.

```bash
cd apps/desktop
npm run typecheck
```

Result: **passed** for renderer, Electron, and E2E TypeScript projects.

`git diff --check f4f4216651d0ab4830f232df8a4d15ee1981022e..b8be4a3182eb9a2c03834a32aecf8beb4c531df3`
also passed.

## Final disposition

All three prior quality findings are genuinely closed. Task 14 is approved at
`b8be4a3182eb9a2c03834a32aecf8beb4c531df3` with no remaining code-quality
findings.
