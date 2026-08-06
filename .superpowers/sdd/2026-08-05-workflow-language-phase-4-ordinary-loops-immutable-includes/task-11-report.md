# Task 11 report: Desktop signal confirmation and dependency inspection

Status: DONE

## Outcome

The Desktop workflow operator surface now recognizes the bounded
`loop_signal_confirmation` interaction shape and presents its existing backend-owned
wire actions as **Accept result**, **Continue with feedback**, and **Cancel**. Feedback
stays disabled until non-empty input is present, final-iteration confirmations omit
feedback when the authoritative `next_actions` omits `provide-input`, and ordinary
`loop_input` plus unknown future interaction shapes retain the generic action copy.

The workflow detail dialog now presents the bounded authenticated compilation
projection already exposed by the backend: selected source identity and precedence,
dependency/expansion counts and include depth, the composite digest, and ignored child
policy badges. The renderer does not read YAML, fetch manifests, infer dependencies,
or display filesystem and sidecar metadata.

All five Desktop locales define the new interaction and dependency copy. Existing
approve/provide-input mutations, interaction IDs, expected versions, conflict refresh,
double-submit protection, attention routing, and terminal behavior remain unchanged.

## Scope and ownership resolution

The task brief listed the interaction, tests, transport types, and locale files but
required dependency details in the existing workflow detail surface. Task 10 exposes
`compilation` on catalog `WorkflowDetail`, not on the public run snapshot. The parent
agent therefore explicitly authorized these two additional Task 11 files:

- `apps/desktop/src/app/workflows/view-workflow-dialog.tsx`
- `apps/desktop/src/app/workflows/view-workflow-dialog.test.tsx`

No run-level compilation field or backend behavior was invented.

## Files changed

- `apps/desktop/src/types/hermes.ts`: added forward-compatible optional compilation
  projection types on `WorkflowDetail`; kept `pending_interaction` open-ended.
- `apps/desktop/src/app/workflows/run-inspector.tsx`: added the exact local signal
  interaction guard and interaction-aware labels while retaining existing wire actions.
- `apps/desktop/src/app/workflows/attention-inbox.tsx`: applied the same guarded labels
  to background attention summaries.
- `apps/desktop/src/app/workflows/view-workflow-dialog.tsx`: rendered the bounded
  catalog-detail dependency diagnostics.
- `apps/desktop/src/app/workflows/index.test.tsx`: covered signal labels, input gating,
  final-iteration behavior, generic/unknown fallback, attention copy, and locale
  completeness.
- `apps/desktop/src/app/workflows/workflow-operations.e2e.test.tsx`: proved accept and
  feedback use the existing compare-and-set request bodies and preserved 409 refresh.
- `apps/desktop/src/app/workflows/view-workflow-dialog.test.tsx`: covered bounded
  diagnostics, disclosure canaries, and old-backend absence.
- `apps/desktop/src/i18n/{ar,en,ja,types,zh-hant,zh}.ts`: added complete localized copy.

## TDD evidence

The first attempted renderer command could not collect tests because this worktree did
not yet have the React runtime installed (`react/jsx-dev-runtime` was unresolved). I
ran the repository's locked `npm ci`; that environment failure is not counted as RED
evidence and did not change the lockfile.

1. Interaction renderer RED:
   - `cd apps/desktop && npm test -- src/app/workflows/index.test.tsx src/app/workflows/workflow-operations.e2e.test.tsx`
   - Valid RED: 43 passed / 7 failed. The signal surface still rendered generic
     **Approve**, **Provide input**, and **Input value** copy; attention lacked
     **Accept result**.
   - Focused GREEN: 51 passed / 0 failed after the guarded presentation mapping and
     existing mutation-path assertions.
2. Dependency-detail RED:
   - `cd apps/desktop && npm test -- src/app/workflows/view-workflow-dialog.test.tsx`
   - Valid RED: 30 passed / 1 failed because the **Workflow dependencies** region did
     not exist.
   - Focused GREEN: 31 passed / 0 failed after rendering only the backend projection.
3. Locale-completeness RED:
   - `cd apps/desktop && npm test -- src/app/workflows/index.test.tsx -t 'catches signal and dependency copy falling back to English in any locale'`
   - Valid RED: 1 failed / 47 skipped because `acceptResult` was undefined.
   - GREEN is included in the 51-test interaction gate and final 82-test gate.

The example `toBeEnabled`/`toBeDisabled` assertions from the brief were expressed with
the native `disabled` property because this Vitest setup does not install the
jest-dom Chai matchers. The tests enforce the same behavior.

## Compatibility and disclosure checks

- The guard requires only a string `interaction_id`, integer `iteration`, integer
  `max_iterations`, and exact `loop_signal_confirmation` type.
- Unknown or malformed interaction shapes keep generic labels.
- `loop_input` keeps **Provide input**.
- Final-iteration feedback is controlled solely by backend `next_actions`.
- Signal accept still posts `approve`; feedback still posts `provide-input` with
  `value`, `expected_version`, and `interaction_id`.
- Existing 409 refresh and action-disable tests pass unchanged in semantics.
- Workflow details without `compilation` remain runnable and omit the new region.
- Private `definition_location` and `sidecar_digest` canaries supplied alongside the
  public projection never render.

## Final verification

- Required focused gate:
  `cd apps/desktop && npm test -- src/app/workflows/index.test.tsx src/app/workflows/workflow-operations.e2e.test.tsx src/app/workflows/view-workflow-dialog.test.tsx`
  — 82 passed / 0 failed.
- `cd apps/desktop && npm run typecheck` — passed all renderer, Electron, and E2E
  TypeScript projects.
- Prettier check over all 13 changed Desktop source/test files — passed.
- ESLint over all 13 changed Desktop source/test files — 0 errors / 28 warnings. The
  28 warnings exactly match the pre-change baseline in the three existing test files;
  production files and newly added lines introduce no lint warnings.
- `git diff --check` — passed.

## Concerns

No scoped production or compatibility concerns remain. `npm ci` reported audit
findings from the existing lockfile; Task 11 did not change dependencies or package
metadata.

## Fix round 1 — interaction-scoped feedback draft

Status: DONE

Review base: `a04dbcb6a42bcc69ed1ae45c92e506f74d577a82`

### Finding addressed

`RunInspector` is reused while the selected run snapshot changes, so its scalar
`inputValue` state survived a completed signal interaction. A later signal confirmation
could therefore render the previous feedback and enable submission before the operator
entered feedback for the new interaction.

The feedback/input draft is now paired with the current backend `interaction_id`.
Rendering a distinct interaction derives an empty value immediately, without an effect
or stale intermediate paint. Editing stores the value against that interaction ID.
Backend `next_actions` still solely determines whether a feedback control exists, and
ordinary `loop_input` or unknown interaction shapes retain their generic labels and
wire behavior.

### Strict TDD evidence

1. RED, before production edits:
   - `cd apps/desktop && npm test -- src/app/workflows/index.test.tsx -t 'catches feedback state being reused across distinct signal confirmations'`
   - 1 failed / 48 skipped. After submitting `Tighten it` for `signal-1` and rerendering
     the same `RunInspector` with `signal-2`, the new feedback input still contained
     `Tighten it` instead of the literal empty value.
2. GREEN, after the interaction-keyed draft:
   - The same focused command passed 1 / 1 selected test with 48 skipped.
   - The new interaction renders an empty input and a disabled **Continue with
     feedback** button.

### Files changed

- `apps/desktop/src/app/workflows/index.test.tsx`: added the real two-interaction
  rerender regression covering entry, submission, new interaction ID, empty value, and
  disabled state.
- `apps/desktop/src/app/workflows/run-inspector.tsx`: replaced the scalar input state
  with the narrow interaction-keyed draft.
- `.superpowers/sdd/2026-08-05-workflow-language-phase-4-ordinary-loops-immutable-includes/task-11-report.md`:
  appended this fix-round evidence.

### Final verification

- Focused regression: 1 passed / 0 failed (48 skipped).
- Exact Task 11 gate:
  `cd apps/desktop && npm test -- src/app/workflows/index.test.tsx src/app/workflows/workflow-operations.e2e.test.tsx src/app/workflows/view-workflow-dialog.test.tsx`
  — 83 passed / 0 failed.
- `cd apps/desktop && npm run typecheck` — passed all three TypeScript projects.
- Prettier check on `run-inspector.tsx` and `index.test.tsx` — passed after applying
  formatting.
- ESLint on the same files — 0 errors / 17 unchanged baseline warnings in
  `index.test.tsx`; `run-inspector.tsx` has no warnings.
- `git diff --check` — passed before and after the report append.

### Self-review and concerns

- The draft key comes only from the existing bounded pending interaction projection;
  no backend state or action vocabulary changed.
- The value mismatch is resolved during render rather than in an effect, so the new
  interaction cannot paint or submit stale text for one frame.
- The requested Minor exact-type malformed-guard case remains intentionally deferred
  to final review and was not entered in this fix round.
- No scoped concerns remain.
