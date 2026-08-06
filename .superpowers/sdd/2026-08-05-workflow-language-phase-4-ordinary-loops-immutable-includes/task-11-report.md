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
