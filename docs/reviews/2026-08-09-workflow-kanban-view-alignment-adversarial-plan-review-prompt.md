# Adversarial design and plan review prompt — Workflow Kanban view alignment

Paste everything below this introductory paragraph into a fresh, capable model
or coding agent with read and shell access to the Hermes repository. The
reviewer must assess the approved Desktop design and executable implementation
plan for aligning Workflows Active board, History, and Archive with the SDK
Kanban plugin's collapsible vertical-lane presentation and right-side detail
drawer.

This is an adversarial **design- and plan-quality review**, not an
implementation task. Try to falsify the design and plan before implementation
begins. Find wrong premises, incomplete requirements, shared-component
regressions, accessibility failures, state and query races, layout failures,
non-executable TDD steps, and acceptance gates that could allow a broken UI to
ship.

Do not modify production code, tests, generated files, existing documentation,
Git history, branches, worktrees, or refs. Do not create a feature branch,
commit, rebase, merge, push, publish, open a pull request, or build a release.
The only authorized repository write is the final review report named under
**Required output**. Existing untracked files are user-owned and must remain
untouched.

## Explicit security exclusion

This review is limited to functional UI, architecture, accessibility,
performance, and plan executability. Do not run a standalone security or threat
review, probe credentials, contact remote services, or report speculative
security hardening. Ordinary query scoping, profile isolation, and action
authority remain in scope as functional correctness.

## Role

You are a skeptical principal-level reviewer experienced with React 19,
TypeScript, TanStack Query and Virtual, nanostores, Electron renderer
architecture, accessible nonmodal detail panes, responsive desktop layouts,
internationalization, and strict test-driven implementation plans.

Your task is to determine whether following the plan exactly will deliver the
approved interaction without breaking the existing Workflows behavior or the
other consumer of the shared `ActivityBoard`.

Assume every plan claim is unproven until you trace it to:

1. the approved design requirement;
2. the current SDK Kanban implementation being used as the visual reference;
3. current Desktop production code and shared-component consumers; and
4. an explicit plan task, RED test, GREEN change, verification command, and
   acceptance criterion.

Do not stop after finding the first gap. Do not treat future files named by the
plan as missing implementation. Review whether the plan creates them in the
right package, wires them through real interfaces, and proves their behavior.
Commit messages, plan length, existing test names, and prior approval are leads,
not proof.

## Repository scope and preservation rules

Repository:
`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent`

At prompt creation:

- development branch: `base`;
- expected `HEAD`: `5c9d8a4e68f2faf4877acc4d08993224704d87de`;
- expected `origin/base`: `786f8dc0175410044000113233bec2bb610e7733`;
- `base` is ahead by the two approved planning commits;
- literal `main` is synchronization-only;
- existing worktrees must not be changed;
- existing untracked `docs/assessments/`, `docs/handoffs/`, and Ericsson review
  artifacts are user-owned; and
- `docs/superpowers/` is ignored by a repository rule even though these two
  reviewed artifacts are committed.

Begin by recording branch, exact SHA, status, and worktree list. If the
repository or review-input hashes differ, stop and report
`REVIEW_INPUT_CHANGED`; do not silently review different inputs and do not
mutate refs to make them match.

## Immutable review inputs

Read both files completely and verify SHA-256 before review:

| Artifact | Expected SHA-256 |
| --- | --- |
| `docs/superpowers/specs/2026-08-09-workflow-kanban-view-alignment-design.md` | `9282fa07bf464db838566439a0860a9dbd1c3d9bbf3d26861e4022674239d907` |
| `docs/superpowers/plans/2026-08-09-workflow-kanban-view-alignment.md` | `71e801c654a6840b364d3cbd0ec192983e5cb854253f7b01c7bc594588eb97e1` |

## Sources of truth to inspect before judging

Read the scoped guidance completely:

1. `AGENTS.md`
2. `apps/desktop/AGENTS.md`
3. `apps/desktop/DESIGN.md`

Inspect the actual SDK Kanban reference, including unchanged helpers used by
the new look and feel:

1. `apps/desktop/src/plugins/kanban/board.tsx`
2. `apps/desktop/src/plugins/kanban/drawer.tsx`
3. `apps/desktop/src/plugins/kanban/ui.tsx`
4. `apps/desktop/src/plugins/kanban/i18n.ts`
5. `apps/desktop/src/plugins/kanban/kanban.css`
6. `apps/desktop/src/plugins/kanban/types.ts`

Inspect the current shared board and both consumers:

1. `apps/desktop/src/components/activity-board/types.ts`
2. `apps/desktop/src/components/activity-board/activity-board.tsx`
3. `apps/desktop/src/components/activity-board/virtual-card-column.tsx`
4. `apps/desktop/src/components/activity-board/activity-board.test.tsx`
5. `apps/desktop/src/components/activity-board/activity-board.performance.test.tsx`
6. `apps/desktop/src/app/kanban/index.tsx`
7. `apps/desktop/src/app/kanban/adapter.ts`
8. `apps/desktop/src/app/kanban/task-inspector.tsx`

Inspect the complete current Workflows composition and relevant state/query
seams:

1. `apps/desktop/src/app/workflows/index.tsx`
2. `apps/desktop/src/app/workflows/index.test.tsx`
3. `apps/desktop/src/app/workflows/adapter.ts`
4. `apps/desktop/src/app/workflows/adapter.test.ts`
5. `apps/desktop/src/app/workflows/attention-inbox.tsx`
6. `apps/desktop/src/app/workflows/run-inspector.tsx`
7. `apps/desktop/src/app/workflows/store.ts`
8. `apps/desktop/src/app/workflows/detail-query.ts`
9. `apps/desktop/src/app/workflows/workflow-operations.e2e.test.tsx`
10. `apps/desktop/src/app/workflows/typed-artifact-view.tsx`
11. `apps/desktop/src/app/workflows/typed-artifact-view.test.tsx`

Inspect primitives and conventions the plan relies on:

1. `apps/desktop/src/components/ui/search-field.tsx`
2. `apps/desktop/src/components/ui/button.tsx`
3. `apps/desktop/src/components/ui/error-state.tsx`
4. `apps/desktop/src/components/ui/loader.tsx`
5. `apps/desktop/src/components/ui/tabs.tsx`
6. `apps/desktop/src/hooks/use-grab-scroll.ts`
7. `apps/desktop/src/lib/persisted.ts`
8. `apps/desktop/src/i18n/types.ts`
9. `apps/desktop/src/i18n/en.ts`
10. `apps/desktop/src/i18n/ar.ts`
11. `apps/desktop/src/i18n/ja.ts`
12. `apps/desktop/src/i18n/zh.ts`
13. `apps/desktop/src/i18n/zh-hant.ts`
14. `apps/desktop/src/i18n/runtime.test.ts`
15. `apps/desktop/eslint.config.mjs`
16. `apps/desktop/package.json`

Use relevant Git history to distinguish intended behavior from accidental
shape, particularly for `ActivityBoard`, Workflows selection persistence,
evidence tabs, focus behavior, and the SDK Kanban lane/drawer redesign.

## Approved delivery intent

The approved design aligns the three Workflows run views with the useful
presentation contract of the SDK Kanban plugin while preserving Workflows as
the authority for lifecycle semantics:

- horizontal strip of fixed-width vertical lanes;
- empty lanes become narrow expandable rails only when the board has work;
- lane state is local, phase-aware presentation state and never persisted;
- compact header with loaded-card count and visible but honestly disabled
  filter/search affordances;
- no profile picker or functional filter/search implementation in this phase;
- explicit run activation opens the existing seven-tab `RunInspector` in a
  nonmodal right-side drawer instead of below the board;
- backend queries, pagination, attention, cleanup, evidence, action authority,
  CAS behavior, and profile routing remain unchanged;
- the shared board's existing grid mode and Kanban-page consumer remain
  behaviorally unchanged; and
- all new copy, keyboard behavior, responsive containment, virtualization, and
  reduced-motion behavior are proved before implementation is accepted.

## Non-negotiable plan invariants

A demonstrated violation is Critical or Important depending on how much of the
feature or an existing consumer it invalidates.

1. **Presentation only.** No backend endpoint, query parameter, workflow state,
   card membership, profile behavior, or action authority changes.
2. **Shared default compatibility.** `ActivityBoard` remains grid by default;
   the existing Kanban page renders, selects tasks, paginates, and virtualizes
   exactly as before.
3. **All three run views align.** Active board, History, and Archive use the
   same collapsible-lane and drawer contract; the Workflows catalog does not
   show run-only controls.
4. **Disabled means unavailable.** Filter and search are native-disabled,
   localized, skipped in normal tab order, and cannot update state, query keys,
   filtering, or network traffic.
5. **Counts are honest.** The header count describes loaded cards, not an
   unpaginated server total, and remains correct after pagination and view
   changes.
6. **Collapse state is exact.** Empty lanes auto-collapse only when any lane has
   cards; all-empty boards stay expanded; user overrides survive only within
   the same scope and empty/occupied phase; polling with unchanged phases is a
   no-op; view changes and remounts reset state.
7. **No card movement.** Collapse, expand, selection, and the visual redesign
   never introduce drag/drop or generic move semantics.
8. **Explicit foreground selection.** Background polling never selects a run
   or opens the drawer. Every user-owned run-opening surface has deterministic
   selection and focus-return behavior.
9. **Inspector authority is preserved.** `RunInspector` remains unchanged and
   owns all seven tabs, evidence activation, draft/reset behavior, action
   labels, enablement, CAS mutations, and recovery controls.
10. **Drawer behavior is bounded.** The drawer is nonmodal, closeable while
    loading or failed, independently scrollable, responsive, and never unmounts
    or resets the board behind it.
11. **Keyboard ownership is exact.** Escape dismisses one appropriate surface,
    focus is not stolen by polling or view navigation, closing returns focus to
    the activation origin when connected and otherwise to a deliberate
    fallback, and nested controls retain their keyboard behavior.
12. **Performance remains bounded.** Large lanes remain virtualized; unchanged
    polls preserve reference identity where promised; horizontal overflow stays
    inside the lane strip; no page-level horizontal overflow appears at 320,
    768, or 1440px.
13. **Design-system compliance.** Existing tokens and primitives are used; no
    raw colors or duplicate search control are introduced; every transition is
    reduced-motion safe.
14. **Localization and accessibility are complete.** Every new user-facing
    string is typed and translated in all five Desktop locales; lanes, cards,
    toolbar controls, drawer, tabs, loading, and failures have truthful native
    semantics.
15. **Failures remain local.** A selected-detail failure does not replace or
    disable the board; action failures and existing query/cancellation behavior
    remain unchanged.
16. **The plan is executable.** Every task names real files or clearly owned new
    files, writes RED before GREEN, predicts the correct failure, uses real
    commands from the correct directory, stages only owned files, and ends with
    the existing workflow UI, type-check, lint, and diff gates.

## Required review method

### 1. Build design-to-plan and task-coverage matrices

- Map every normative design requirement and acceptance criterion to concrete
  plan tasks and tests.
- Rate every plan task `complete`, `partial`, `missing`, or `contradicted`.
- Identify orphan design requirements and plan work without design authority.
- Verify ordering: no test or GREEN step may rely on a file/helper added only
  by a later task.

### 2. Compare the intended look and feel with the SDK Kanban source

Trace actual lane widths, collapsed rails, all-empty behavior, scroll
containment, card treatment, filter/search hierarchy, drawer placement,
loading/error behavior, Escape handling, and board-preservation behavior.
Distinguish intentional Workflows adaptations from accidental divergence. Do
not require SDK drag/drop, persistence, filters, or profile controls that the
approved scope explicitly excludes.

### 3. Audit shared `ActivityBoard` compatibility

- Enumerate every production consumer and current prop contract.
- Verify the proposed discriminated props are type-safe and preserve grid
  behavior without unnecessary state/effects or event-shape changes.
- Check callback variance for `onOpenCard(card, origin?)` and every caller.
- Verify card keys, focus under reorder, pagination, stale-state rendering,
  empty columns, badge tones, source labels, and the 1,000-card virtualization
  contract.
- Validate whether `useGrabScroll` can coexist with nested vertical scrolling
  and every native button without intercepting selection.

### 4. Falsify the lane-state algorithm

Attack initial render, all-empty boards, empty→occupied→empty transitions,
occupied→empty→occupied transitions, removed/added/reordered lanes, unchanged
polls with new object identities, scope changes, rapid toggles before effects
flush, remounts, Strict Mode behavior, and simultaneous pagination/refetch.
Confirm the planned code cannot resurrect stale overrides or visibly render a
stale collapse state for a frame.

### 5. Trace Workflows query, selection, and action behavior

Follow selected-run identity from the persisted nanostore through view changes,
card activation, Attention inbox activation, selected detail/event queries,
query cancellation, action mutations, 409 reconciliation, invalidation, and
background polling. Check every route by which a drawer can open or switch runs,
not only `ActivityBoard.onOpenCard`. Confirm loaded counts and pagination are
computed from the right model and do not double-count.

### 6. Audit drawer, inspector, keyboard, and focus behavior

- Validate complementary-region semantics versus modal/dialog semantics.
- Check initial focus, tab order, `aria-expanded`, `aria-controls`, accessible
  naming before detail loads, close button discoverability, and fallback focus.
- Trace Escape through the global window listener, Radix tabs/dialogs/selects,
  input confirmations, and any other nested control that may consume Escape.
- Check rapid card switching, close during loading, failure then close, query
  completion after close, source card unmount from virtualization/filter/view,
  and selecting a run from Attention while another drawer is open.
- Verify keying `RunInspector` resets only what should reset and does not disturb
  evidence/action semantics.

### 7. Audit responsive layout and performance

Reconstruct the actual flex/min-height/overflow chain from page root through
Attention, lane strip, lane, virtualizer, cleanup, and drawer. Confirm the plan
can produce full-height lanes, independently scrolling drawer content, contained
horizontal scrolling, and usable narrow layouts without hiding cleanup or
creating nested-scroll traps. Inspect whether tests assert computed behavioral
contracts or only class fragments.

### 8. Audit localization, styling, and accessibility

Verify every proposed locale key, function signature, and test location against
the catalogs. Check plural/count truthfulness, RTL behavior, vertical writing,
button disabled styling, contrast tokens, selected/accent borders, health and
badge tones, reduced motion, and native roles/states. Literal strings in tests
are acceptable; literal user-facing strings in production JSX are not.

### 9. Audit strict-TDD executability

For every task:

- confirm existing paths, symbols, component props, test helpers, script names,
  and package commands;
- confirm each predicted RED fails for the stated reason rather than an earlier
  compile/import/setup error;
- confirm GREEN snippets are internally type-consistent and complete enough to
  implement without design invention;
- confirm neighboring regressions are included at the right task boundary;
- confirm staging scopes cannot capture unrelated user files;
- reject placeholder instructions or conditional implementation choices; and
- confirm no production change to `RunInspector`, SDK Kanban, backend, plugin,
  Electron main process, dependencies, config, or environment variables is
  silently required.

Run only small existing tests when needed to validate a premise. This is a plan
review, so do not run multi-hour suites for code that does not exist and do not
create test files in the repository.

## Specific plan premises to challenge

Reach an explicit `supported`, `unsupported`, or `insufficiently established`
verdict on each premise:

1. The SDK Kanban board's useful look and feel can be reproduced through the
   shared Desktop `ActivityBoard` without importing plugin implementation code.
2. An opt-in discriminated prop mode preserves the current Kanban page and all
   existing grid behavior.
3. A second optional callback argument containing `HTMLButtonElement` is safe
   for every current caller and test double.
4. The proposed pure lane reconciliation plus React effect correctly implements
   the approved phase/reset rules under polling, rapid input, and Strict Mode.
5. `useGrabScroll` is the correct existing primitive for the proposed nested
   horizontal-lane/vertical-column interaction.
6. `w-64` expanded lanes, 2rem rails, the proposed flex chain, and the current
   virtualizer measurement produce the required responsive full-height layout.
7. The loaded count derived from `model.columns[].cards.length` is truthful for
   every run view and pagination state.
8. A native-disabled `SearchField` can use the existing required `onChange`
   contract without emitting state changes or leaving interactive adornments.
9. The proposed header composition preserves tab semantics, focus, RTL, and
   narrow-width usability.
10. The existing selected-run query can be projected into a drawer without
    changing keys, intervals, invalidations, or cancellation behavior.
11. `selected.error`, `selected.isLoading`, and `selected.data` are sufficient
    and correctly typed to drive closeable drawer failure/loading/content
    states.
12. A window-level Escape listener guarded by `defaultPrevented` closes exactly
    one surface and does not conflict with nested inspector controls.
13. Keeping the board mounted behind the drawer preserves lane scroll,
    virtualization, pagination, and selection across loading and refetches.
14. The focus-return ref is correct for ActivityBoard cards, Attention inbox,
    rapid run switching, view changes, card unmount, and drawer failure.
15. Keying `RunInspector` by `run.run_id` resets tabs/drafts for a different run
    while preserving all established evidence and action behavior.
16. The proposed locale additions and regression assertions catch every new
    production string and do not create English fallback or grammar regressions.
17. The listed focused tests plus `test:workflow-ui`, type-check, lint, and diff
    checks cover both Workflows and the shared Kanban consumer.
18. The implementation can be completed without modifying `RunInspector`, SDK
    Kanban files, backend code, plugin code, dependencies, persisted config, or
    environment variables.

## Finding severity and verdict

- **CRITICAL** — the plan rests on a false architectural/state premise that
  requires redesign, corrupts workflow authority, or breaks an existing major
  Desktop surface.
- **IMPORTANT** — a realistic missing or incorrect step could ship materially
  broken selection, drawer, keyboard, accessibility, responsive, pagination,
  virtualization, query/action, locale, or shared-board behavior; or could make
  the TDD sequence non-executable.
- **MINOR** — a bounded plan-quality, diagnostic, maintainability,
  documentation, or test-clarity issue with a concrete consequence that does
  not block implementation by itself.

Do not inflate severity. Do not report style preferences, speculative future
features, or unsupported concerns. A missing test alone is Important only when
a release-critical behavior lacks another credible verification path.

Verdict:

- `BLOCK` if any Critical or Important finding remains;
- `CONDITIONAL` if only Minor findings remain but one requires an explicit
  product decision; or
- `READY FOR IMPLEMENTATION` only when no Critical/Important finding remains
  and every required matrix is complete.

## Ten-element finding proof standard

Every finding must include:

1. stable ID and severity;
2. concise title;
3. affected design section, plan task, and production surface;
4. exact plan text or omission plus current source evidence that contradicts
   it;
5. violated invariant or approved decision;
6. realistic implementation/runtime scenario;
7. concrete wrong result and user/operator consequence;
8. why another task, test, current framework, or acceptance gate does not
   already cover it;
9. the smallest plan/design correction that fixes the whole gap without scope
   creep; and
10. exact RED test, verification command, or acceptance criterion to add or
    change.

If any element is missing, put the concern in the unresolved-question or
verification-ledger section rather than presenting it as a finding. Do not
disguise speculation as residual risk.

## Evidence and command discipline

Start with:

```bash
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent status --short --branch
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent branch --show-current
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent rev-parse HEAD
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent rev-parse origin/base
git -C /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent worktree list --porcelain
shasum -a 256 \
  /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/specs/2026-08-09-workflow-kanban-view-alignment-design.md \
  /Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/superpowers/plans/2026-08-09-workflow-kanban-view-alignment.md
```

Use `rg`, `git log`, `git show`, `git diff`, direct source inspection, static
imports, existing test collection, and harmless package-script inspection.
When the plan references an existing function, test, command, workspace, prop,
or API, inspect it. When it references a new artifact, verify package ownership
and callers. Record commands actually run; do not report unrun commands as
passing.

Do not use another agent's report as proof. Reduce every finding to direct
design, plan, source, history, and command evidence.

## Required output

Write the review to:

`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent/docs/reviews/2026-08-09-workflow-kanban-view-alignment-adversarial-plan-review-fable-5.md`

The report must contain:

1. exact repository state, input hashes, model, platform, date, and evidence
   sources reviewed;
2. overall verdict: `BLOCK`, `CONDITIONAL`, or `READY FOR IMPLEMENTATION`;
3. findings table sorted by severity followed by the complete ten-element proof
   for every finding;
4. design-requirement-to-plan traceability matrix;
5. all-task coverage matrix for Tasks 1–6;
6. SDK-Kanban-reference parity/adaptation matrix;
7. shared-`ActivityBoard` consumer compatibility matrix;
8. Workflows state/query/action preservation matrix;
9. exact verdict on all sixteen non-negotiable invariants;
10. exact verdict on all eighteen specific plan premises;
11. lane-state edge-case assessment;
12. drawer, keyboard, focus, accessibility, responsive-layout, and performance
    assessment;
13. strict-TDD command/file/type/ordering audit;
14. what was verified complete and why, including production paths inspected;
15. required plan/design corrections ordered by severity and dependency;
16. unresolved product decisions, unverified premises, and evidence required to
    resolve them;
17. source-grounding coverage for every cited existing path/symbol, including
    `VERIFIED`, `MISSING`, `AMBIGUOUS`, or `UNCHECKABLE` verdicts; and
18. command/evidence ledger with exact command, working directory, result,
    duration where meaningful, and evidence kind.

If there are no findings, say so explicitly and still provide every required
matrix and verdict. Do not use plan length, task count, test names, or confident
prose as a substitute for evidence.

End the report with exactly one of:

- `IMPLEMENTATION MUST NOT BEGIN UNTIL CRITICAL AND IMPORTANT PLAN FINDINGS ARE RESOLVED.`
- `IMPLEMENTATION MAY BEGIN ONLY AFTER THE LISTED PRODUCT DECISIONS ARE APPROVED.`
- `THE REVIEWED DESIGN AND PLAN ARE READY FOR IMPLEMENTATION.`

Do not implement corrections. Stop after writing the report.
