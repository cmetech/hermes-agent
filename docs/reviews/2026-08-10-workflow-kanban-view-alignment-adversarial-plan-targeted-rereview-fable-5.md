# Targeted adversarial re-review — Workflow Kanban view alignment (F1–F8 closure) — Fable 5

## 1. Repository state, inputs, environment

| Item | Value |
| --- | --- |
| Repository | `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent` |
| Branch | `base` (`## base...origin/base [ahead 3]`) |
| `HEAD` | `fb061582fb496d59e13316116701e34a4ba90d09` — matches prompt |
| `origin/base` | `786f8dc0175410044000113233bec2bb610e7733` — matches prompt |
| Worktrees | 44 worktrees listed; none touched |
| Untracked files | Only the pre-existing user-owned `docs/assessments/`, `docs/handoffs/`, Ericsson review artifacts, the two prior review prompts/reports, and this re-review prompt — untouched |
| Design SHA-256 | `286ffb2bf96d40c3a9d56ebb334faac0b819f6fb6ad00eba580e8edd69a2e942` — matches prompt (revised, "Revision: 2026-08-10 — adversarial findings F1–F8 resolved", design line 7) |
| Plan SHA-256 | `d6492ae795e694bb1d0667e0855ef8380f1a5d54d2e484b40cd47e149098afd6` — matches prompt |
| Prior review report SHA-256 | `cd394ad2b3ba3371e54429703f1a8323ba0166848f7f012cc8f7d8325937bb58` (recorded as instructed; treated as the finding record only, not as evidence) |
| Reviewer model | Claude Fable 5 (`claude-fable-5`) |
| Platform / date | macOS (Darwin 25.5.0) / 2026-08-10 |

`REVIEW_INPUT_CHANGED` does **not** apply — `HEAD` and both revised input
hashes match the prompt. `git diff --stat 5c9d8a4e6..HEAD` shows the only
commits since the prior review changed exactly the two immutable inputs
(`fb061582f docs(desktop): resolve workflow kanban plan review`), so every
production-source fact verified in the prior review remains verifiable against
an unchanged tree — and each fact relied on below was re-checked directly.

**Inspected sources (this re-review, all read from the working tree at the
pinned HEAD):** both revised immutable inputs in full; the prior finding record
in full; `AGENTS.md` (root — section map plus the branch-terminology and
testing/contribution sections), `apps/desktop/AGENTS.md` in full,
`apps/desktop/DESIGN.md` form-controls/layout sections (lines 140–175);
`src/components/activity-board/{activity-board.tsx,virtual-card-column.tsx,activity-board.test.tsx,activity-board.performance.test.tsx,types.ts}`
in full; `src/app/workflows/{index.tsx,attention-inbox.tsx}` in full;
`src/app/workflows/index.test.tsx` (helper/mock block lines 1–200, locale
regression lines 870–980, plus a full-file grep proving no existing
`complementary`/"run inspector" role queries); `src/app/workflows/run-inspector.tsx`
targeted (landmark labels); `src/app/kanban/index.tsx` in full;
`src/i18n/{types,en,ar,ja,zh,zh-hant}.ts` targeted (each `operations` block
opening at the plan-cited line; key presence/absence greps);
`src/types/hermes.ts` (`WorkflowAttentionItem`, health union);
`src/hooks/use-grab-scroll.ts`, `src/components/ui/loader.tsx`,
`src/components/ui/search-field.tsx`, `src/styles.css` (token greps). Exact
commands are in §9.

## 2. Overall verdict

**`READY FOR IMPLEMENTATION`** — all eight findings F1–F8 are **CLOSED** in
the revised design and plan, each at the correct task boundary with credible
RED→GREEN verification. The narrow regression scan over the corrected regions
found **no Critical or Important regression**; two Minor clarity items (§5)
have concrete but self-diagnosing consequences (the plan's own Step 8 gates
catch both) and require no product decision.

## 3. F1–F8 closure table

| ID | Status | Direct evidence (design/plan/source) | Rationale |
| --- | --- | --- | --- |
| F1 distinct landmarks | **CLOSED** | design 316–319, 500–501, 551–553; plan 935/946/957/968/979 (`workflowRunDrawerLabel` in 5 locales), 1364–1372, 1212–1216 + 1247–1259, 1491–1495; `run-inspector.tsx:196`; grep: 0 hits for `workflowRunInspectorLabel` in design+plan | Drawer named `<workflow> run details` in every locale; inner inspector landmark untouched; unit and integration tests assert two real `complementary` roles with distinct names; stale key fully purged |
| F2 grid callback assertion | **CLOSED** | plan 582–589 (explicit replacement of the exact one-argument assertion, declared RED), 568–579, 591–600, 758; `activity-board.test.tsx:33` | The pre-existing exact `onOpenCard(card)` assertion is amended in Task 3 Step 2 — before implementation Step 6 — with a truthful RED reason; GREEN follows for both appearances |
| F3 height chain + virtualization | **CLOSED** | design 172–177, 358–374, 543–545; plan 654–663 (root), 670 (strip `min-h-0 flex-1`), 736 (lane `h-full` / body `min-h-0 flex-1` + `data-lane-scroll`), 1719–1724, 1751–1763, 1455–1464, 784–827, 1853 | The full seven-link chain is specified and class-asserted end-to-end; Attention/cleanup are `shrink-0` rows; catalog and drawer scroll separately; lane-mode 1,000-card test exists with an in-file fixture; 300-card/1440px launch check added |
| F4 Attention focus origin | **CLOSED** | plan 56, 1426, 1652–1657, 1682–1695, 1522–1556, 1850; design 326–329; `attention-inbox.tsx:8,56–60`; `en.ts:2129`; `hermes.ts:801–832` | `attention-inbox.tsx` is owned in the file map and Task 6; the button supplies `event.currentTarget`; card and Attention paths converge on `openRunId`, which always replaces the origin; the card A → Attention B → close → Attention-focus integration test is present and fixture-correct |
| F5 workflow-only card chrome | **CLOSED** | plan 740–767 (`lane ?` class ternary, `lane &&` selection, lane-only `borderLeftColor`, lane-only badge compaction), 568–579, 1855; design 240–246, 549–550; `virtual-card-column.tsx:58`; `kanban/index.tsx:57` | All new chrome is gated on `appearance === 'lane'`; the grid class string is byte-identical to production; a grid-preservation regression test asserts old chrome while accepting the origin argument |
| F6 truthful RED/GREEN | **CLOSED** | plan 868–903 (locale RED before keys), 983–991, 1077–1080 (module failure isolated), 1627–1631 + 1639–1642 (guards classified), Task 6 S1–S3 before S5–S8 | Task 4 now runs the locale regression RED first; header-module RED is isolated afterward with an explicit note; Task 6 explicitly classifies navigation-focus and background-refetch as pre-existing guards; every remaining RED claim was re-traced and is truthful |
| F7 lane-mode perf coverage | **CLOSED** | plan 51, 446, 784–808 (`thousandCardModel()` extraction used by the existing grid test), 810–827 (lane case), 838–840; `activity-board.performance.test.tsx:8–12` (file-wide 600px stub) | Task 3 Step 7 is the explicit modifying step; the fixture is file-local and in scope for both tests; the lane test runs under the same 600px rect stub; both appearances gate `< 100` mounted buttons |
| F8 RTL-safe header | **CLOSED** | plan 1123 (`me-2`), 1141 (`ms-auto` + `data-workflow-run-toolbar`), 1053–1068 (Arabic test asserts `ms-auto`, rejects `ml-auto`), 1856; design 424–425, 527 | Implementation uses logical utilities, the toolbar has a stable selector, the Arabic test enforces it, the design mandates logical alignment, and the acceptance checklist covers the RTL result |

## 4. Detailed closure proofs

### F1 — distinct drawer and inspector landmarks — CLOSED

- **Design:** lines 316–319 now require the outer drawer name to be equivalent
  to `<workflow> run details` and explicitly distinct from the unchanged inner
  `RunInspector` landmark `<workflow> run inspector`; testing bullet 500–501
  and acceptance criterion 12 (551–553) pin it.
- **Plan, correct boundary:** Task 4 Step 4 introduces `workflowRunDrawerLabel`
  with en `` `${workflow} run details` `` and translations in all five locales
  (935, 946, 957, 968, 979) — each visibly distinct from "run inspector"
  (the inner label is pre-existing hard-coded English,
  `run-inspector.tsx:196`, and the plan keeps that file read-only per Task 5
  Files and line 1837). Task 5 Step 3 uses it for the drawer aside (1364,
  1366–1372).
- **Real roles in both test tiers:** the Task 5 unit test's `RunInspector` mock
  renders a genuine `<aside aria-label={`${run.workflow} run inspector`}>`
  (1212–1216), and the first drawer test asserts both named `complementary`
  regions plus `getAllByRole('complementary')` length 2 (1247–1259). Task 6
  Step 2's integration test renders the **real** inspector inside the drawer
  and asserts both distinct landmark names and the count of 2 (1491–1495).
  With `workflow: 'Laptop diagnostic'` the two names are
  `'Laptop diagnostic run details'` vs `'Laptop diagnostic run inspector'` —
  `getByRole` never sees multiple matches. During loading the outer name falls
  back to `'run-1 run details'`, still non-colliding.
- **No stale instruction:** `grep -c workflowRunInspectorLabel` over the
  revised design and plan returns 0 for both.
- **No collateral breakage:** grep proves no existing test in
  `index.test.tsx` or `workflow-operations.e2e.test.tsx` queries the
  `complementary` role or the "run inspector" name, so the second landmark
  breaks nothing pre-existing.

### F2 — existing grid callback assertion — CLOSED

- `activity-board.test.tsx:33` still holds the exact one-argument assertion
  `expect(open).toHaveBeenCalledWith(model.columns[0]!.cards[0])`.
- Task 3 Step 2 (582–589) now explicitly instructs: "replace the existing
  first test's exact one-argument assertion with
  `…toHaveBeenCalledWith(model.columns[0]!.cards[0], expect.any(HTMLButtonElement))`"
  and truthfully declares it "intentionally RED before Step 6 because grid
  cards do not yet supply the native-button origin". Step 3's expected-failure
  text (591–600) names the same reason.
- Boundary check: the amendment sits in the test-authoring step (Step 2),
  before the implementing Step 6, which wires
  `onClick={event => onOpenCard(card, event.currentTarget)}` in the shared
  renderer (758) — GREEN then holds for the grid assertion, the new lane
  origin test (547–566), and the grid-preservation test (568–579), which also
  asserts the two-argument call. The RED→GREEN pair is truthful for both
  appearances.

### F3 — bounded full-height overflow and virtualization — CLOSED

The exact planned chain was reconstructed link by link:

1. **Full-height page root** — plan 1719–1724: `main` gets
   `relative flex h-full min-h-0 min-w-0 flex-col overflow-hidden`; design
   358–359.
2. **Non-scrolling run-view flex column** — plan 1752:
   `flex min-h-0 flex-1 flex-col overflow-hidden` with
   `data-workflow-run-view`; design 364.
3. **Flex-grown board shell** — plan 1756: `min-h-0 flex-1` with
   `data-workflow-board-shell`; design 367–369.
4. **Height-aware ActivityBoard root** — plan 654–663:
   `cn('min-w-0', collapsible && 'flex h-full min-h-0 flex-col')`.
5. **Flex-grown horizontal strip** — plan 670:
   `flex min-h-0 min-w-0 flex-1 gap-2 overflow-x-auto overscroll-contain`
   (the `flex-1` whose omission was the core of the original F3, now
   mirroring the SDK reference).
6. **Full-height lane** — plan 736: `flex h-full w-64 shrink-0 flex-col`.
7. **Independently scrolling lane body** — plan 736: lane appearance uses
   `min-h-0 flex-1`, retains `ref={parent}` and "vertical overflow
   containment" (the existing `overflow-y-auto overscroll-contain`,
   `virtual-card-column.tsx:41`), and carries `data-lane-scroll` so the
   contract is testable without computed layout.

Siblings: Attention renders in a `shrink-0` row (plan 1753–1755; design
365–366); the cleanup section is a `shrink-0` row preserving its complete
existing JSX (plan 1759–1761; design 370–372); the catalog gets its own
`min-h-0 flex-1 overflow-y-auto` wrapper (plan 1765; design 361–363); the
drawer body scrolls independently (`min-h-0 flex-1 overflow-y-auto`, plan
1378; design 331–332).

Verification: Task 6 Step 1 asserts the full class chain from the semantic
`main` root through run view, board shell, `h-full` board root, `flex-1`
strip, and `overflow-y-auto` lane body (1455–1464), with plan 1772–1775
explicitly forbidding a test-only root attribute or descendant-only
inference. Task 3 Step 7 asserts strip `overflow-x-auto` + `flex-1`, root
`min-w-0` + `h-full`, and `[data-lane-scroll]` `overflow-y-auto` (771–782).
The lane-mode 1,000-card test (810–827) uses the file-local
`thousandCardModel()` fixture (784–808) under the file-wide 600px
`getBoundingClientRect` stub (`activity-board.performance.test.tsx:8–12`) and
gates `< 100` buttons. Because jsdom cannot prove computed layout, the final
acceptance checklist adds the launch-level check: "At 1440px, a 300-card lane
scrolls inside that lane, the page does not scroll vertically, and
virtualization remains active" (plan 1853; design acceptance criterion 8,
543–545). The design-internal tension flagged in the original review is
resolved: the revised Page composition section (358–374) reads exclusively as
a non-scrolling column.

### F4 — Attention replaces stale focus origin — CLOSED

- **Ownership:** `attention-inbox.tsx` appears in the file responsibility map
  ("Foreground activation origin for drawer focus return", plan 56) and in
  Task 6's Modify list (`attention-inbox.tsx:6-64`, plan 1426).
- **Origin supply:** plan 1685–1693 widens `AttentionInboxProps.onOpenRun` to
  `(runId: string, origin?: HTMLButtonElement)` and changes the existing
  native button (`attention-inbox.tsx:56–60` verified) to
  `onClick={event => onOpenRun(item.run_id, event.currentTarget)}`.
- **Convergent helper:** `openRunId(runId, origin)` unconditionally executes
  `runReturnFocusRef.current = origin ?? null` before selecting (plan
  1652–1655) — it *replaces* the origin even when none is supplied, so an
  older origin can never be retained. The card path delegates through it
  (`openRun`, 1657) and the inbox is wired `onOpenRun={openRunId}` (1695,
  1754). The only other foreground selector, `ReviewRunDialog.onRunLocated`
  (`index.tsx:380–381`, untouched), is reachable only from the catalog view,
  which is only entered through `changeView` (or initial mount), both of
  which null the ref (1673–1677) — so its drawer close deliberately falls to
  the heading fallback, matching design 326–329.
- **Integration test:** plan 1522–1556 performs exactly card A → Attention
  run B → close → asserts `document.activeElement` is the Attention button
  and **not** card A. Fixture correctness verified: the accessible name
  `'Open run Second workflow'` matches `en.ts:2129`
  (`` openWorkflowRun: workflow => `Open run ${workflow}` ``), and the
  attention item supplies every required `WorkflowAttentionItem` field with
  valid literals (`health: 'user_wait'`, `kind: 'approval'`,
  `status: 'paused'`, `node_id` string — `hermes.ts:801–832`). Acceptance
  checklist line 1850 pins the behavior.

### F5 — workflow-only card chrome — CLOSED

- Plan 740–762: the card `className` is a `lane ? <new chrome> : <grid>`
  ternary whose grid branch is byte-identical to production
  (`'block w-full rounded-sm bg-(--ui-bg-quaternary) p-3 text-left focus-visible:outline focus-visible:outline-(--ui-accent)'`,
  `virtual-card-column.tsx:58`); the selected treatment is
  `lane && selected && …`; the health accent `borderLeftColor` is applied
  only in the `lane ?` style branch (grid keeps bare `virtualStyle`, exactly
  today's inline style); and plan 764–767 keeps the grid title/exact-state/
  badge markup byte-for-byte, compacting hierarchy and applying badge tones
  "in lane appearance only".
- Grid consumers stay inert: `KanbanView` (`kanban/index.tsx:57`) passes no
  `layout`/`selectedCardId`, so `aria-expanded` renders `undefined` (absent)
  and no lane class/style path activates.
- The shared-board regression test (plan 568–579) proves grid chrome
  (`rounded-sm`, `bg-(--ui-bg-quaternary)`, no `border-l-2`) while accepting
  the new `(card, origin)` callback — the exact guard F5's correction (a)
  demanded. Design 240–246 and acceptance criterion 11 (549–550) plus
  checklist 1855 record the product position (grid unchanged; no decision
  needed).
- Neutral note: the plan adds `data-activity-card-id` (753) and the origin
  argument to grid cards. Neither is chrome — no class, style, or visual
  hierarchy changes for the Kanban page, and no Kanban test asserts markup.
  This does not reopen F5.

### F6 — truthful RED/GREEN sequencing — CLOSED

- **Task 4 reorder:** Step 1 extends the locale regression at
  `index.test.tsx:890` (verified present with `stringKeys`/`english`/`copy`
  exactly as the plan assumes) **before** any key exists; Step 2 runs it and
  the predicted failure is real (five absent string keys fail `toBeTypeOf`,
  and calling absent functions throws); Steps 3–4 add the keys; Step 5 goes
  GREEN. The header-module RED is isolated to Steps 6–7 with the explicit
  note "The locale-only RED was already proved and resolved in Steps 1–5"
  (1077–1080).
- **Task 6 classification:** lines 1627–1631 explicitly declare the
  navigation-focus test "a pre-existing regression guard [that] should
  already pass", the background-refetch test a pre-passing invariant guard,
  and the inspector-reset assertion the RED of Step 3; Step 4's expectation
  (1639–1642) repeats the split. Both guard claims were re-traced against
  current `index.tsx` (tab click already clears selection at 290–293; no
  polling path writes the store) and hold before and after the change.
- **Residual RED audit (all Tasks 3–6 test steps re-traced against current
  source):** T3 S1 lane tests fail on missing rail/`aria-expanded` controls
  (the region names themselves already exist — failure reason as stated);
  T3 S2 both tests fail on the missing origin argument / missing
  `aria-expanded` as stated; T6 S1 composition tests fail on the absent
  loaded-count label; T6 S2 drawer tests fail on the absent
  `'… run details'` landmark and Close button; T6 S3 inspector-reset fails
  because the current inline `RunInspector` is unkeyed. No test is claimed
  RED for a wrong reason, and no pre-implementation test consumes a helper
  or key introduced by a later task (T4 copy precedes the T6 tests that
  query it; T5's drawer precedes T6; T2's state functions precede T3).

### F7 — lane-mode performance coverage — CLOSED

- Task 3's Files list still marks
  `activity-board.performance.test.tsx:1-58` Modify (446), and Step 7 now
  contains the explicit modifying instruction: "First extract the existing
  1,000-card fixture construction … into a file-local
  `thousandCardModel(): ActivityBoardModel` helper and use it in the existing
  grid test. Then add lane-mode coverage using the same helper and 600px rect
  stub" (784–789).
- The fixture (790–808) is file-local, so it is in scope for both the
  existing grid test and the new lane test; the 600px
  `getBoundingClientRect` stub is installed file-wide in `beforeAll`
  (`activity-board.performance.test.tsx:8–12`), so the lane test runs under
  it.
- Gates: the existing grid test keeps `< 100` buttons
  (`activity-board.performance.test.tsx:52`), the new lane test asserts
  `< 100` (827), and Step 8's expectation names "both grid and collapsible
  1,000-card tests mount fewer than 100 buttons" (838–839). Design 484–485
  requires virtualization in both appearances. Button census in lane mode
  (1 collapse control + virtualized cards, `nextCursor: null`) stays far
  under the gate.

### F8 — RTL-safe header spacing — CLOSED

- Implementation: the heading uses `me-2` (plan 1123) and the toolbar cluster
  `ms-auto` with the stable `data-workflow-run-toolbar` selector (1141) — no
  physical `ml-*`/`mr-*` remains anywhere in the Task 4 Step 8 snippet.
- Test: the Arabic-locale header test (1053–1068) renders under
  `initialLocale="ar"`, selects the toolbar by its data attribute, and
  asserts `toContain('ms-auto')` **and** `not.toContain('ml-auto')`.
- Design: the Responsive section now mandates "Header spacing uses logical
  inline utilities so the toolbar anchors to the correct end in both LTR and
  RTL locales" (424–425), and the Workflows page test list includes "Arabic
  renders the run toolbar with logical end alignment" (527).
- Acceptance: checklist line 1856 — "Arabic toolbar spacing uses logical
  inline utilities and anchors to the correct visual end."

## 5. Narrow correction-regression findings

Scanned per the prompt: TypeScript prop compatibility, test-fixture scope,
TDD ordering, height-chain class assertions vs proposed JSX, distinct-landmark
query behavior, Attention name/fixture correctness, staging scope. Two Minor
findings; no Critical, no Important.

### R1 — MINOR — `collapsibleLanes` JSX must be constructed inside the collapsible-guarded branch

- **Evidence:** Task 3 Step 5 says "Assign the following JSX to
  `collapsibleLanes`" (plan 665–698) and that JSX reads `props.laneCopy.…`
  three times (682–685). Under the Step 4 discriminated union (607–624),
  `props.laneCopy` only type-narrows where the aliased discriminant
  `collapsible` guards the reference. A literal reading — an unconditional
  `const collapsibleLanes = (…)` above the root ternary — fails
  `npx tsc -p . --noEmit` (`laneCopy` does not exist on
  `GridActivityBoardProps | CollapsibleActivityBoardProps`) and, if forced
  past types, would evaluate `props.laneCopy.collapse(...)` eagerly in grid
  renders and throw.
- **Consequence:** bounded and self-diagnosing — the Step 8 type-check/test
  gate fails immediately; no path ships broken. The plan's own "two layout
  branches" phrasing (649) already points at the guarded structure.
- **Smallest correction:** one clarifying sentence in Step 5, e.g. "construct
  `collapsibleLanes` inside the `collapsible` branch (or inline it in the
  ternary's true arm) so the discriminated props narrow." No test change
  needed; the existing Step 8 gate is the verification.

### R2 — MINOR — width-table test "extend" is ambiguous between add and convert

- **Evidence:** Task 3 Step 7 says "Extend the existing width table test to
  render collapsible mode and assert: …" (771–782). The existing test
  (`activity-board.test.tsx:54–60`) also asserts
  `within(container).getByRole('region', { name: 'Active, 1' }).className`
  contains `min-w-0`. The planned expanded-lane container is
  `flex h-full w-64 shrink-0 flex-col` (736), which does not contain
  `min-w-0`. If "extend" is read as *converting* the render to collapsible
  mode while retaining the old region assertion, the Step 8 GREEN gate fails
  on that assertion (and grid-width containment coverage is silently lost);
  if read as *adding* a second, separately-scoped collapsible render, all
  assertions pass and both modes stay covered.
- **Consequence:** bounded — worst case is a self-explaining Step 8 test
  failure with an obvious one-line resolution; no production defect.
- **Smallest correction:** one sentence in Step 7: "keep the existing grid
  render and its assertions; add a second collapsible-mode render in the same
  width table for the new assertions" (or add `min-w-0` to the lane section
  class, which is harmless beside `w-64 shrink-0`).

Also checked and clean: the Attention fixture and accessible name (§4 F4);
`HEALTH_TONE` keys exactly match the `ActivityBoardCard['health']` union
(`types.ts`); all 13 referenced `--ui-*` tokens exist in `styles.css`;
`useGrabScroll` still exports `{ grabbing, onMouseDown }`; `Loader` still
supports `label` + `type="lemniscate-bloom"`; `t.common.close` is `'Close'`;
`SearchField` has no `disabled` today and its test file is absent as Task 1
claims; the new i18n keys are absent from every catalog today (grep count 0),
so the Task 4 RED is genuine; all six plan-cited i18n `operations` insertion
ranges open at the cited lines (types 1676 / en 2004 / ar 1660 / ja 1836 /
zh 2196 / zh-hant 1777); and every `git add` in Tasks 1–6 stages only
plan-owned paths (the Task 3 directory add covers only the five existing
board files plus the two new lane-collapse files — verified by listing), with
Task 6's `git diff --cached --check`/`--name-only` guard keeping pre-existing
user-owned untracked files out.

## 6. Dependency and TDD-ordering audit — Tasks 3–6

| Task | Test-first order | Cross-task dependencies | Verdict |
| --- | --- | --- | --- |
| T3 | S1–S2 author tests (incl. the amended grid assertion, F2) → S3 verifies RED with correct reasons → S4–S6 implement → S7 adds responsive/perf assertions (no RED claimed; lane perf test is a post-implementation guard) → S8 gate | Consumes T2's `ActivityBoardLaneCopy`/state functions (committed in T2 S6); `useGrabScroll`, `cn` exist today | Sound. R1/R2 (§5) are the only clarity risks; both self-diagnose at S8 |
| T4 | S1 extends the locale regression → S2 RED on missing keys (truthful) → S3–S4 add keys → S5 GREEN → S6 header tests → S7 RED isolated to the missing module (locale RED explicitly noted as already resolved) → S8 implement → S9 gate | Consumes T1's disabled `SearchField` (T1 precedes); `workflowViews`/`activeBoard`/`Codicon filter`/`Button` variants all verified present | Sound; the F6 reorder is fully realized |
| T5 | S1 drawer tests (mock renders a real inner `complementary`, distinct-name assertion) → S2 RED on missing module → S3 implement → S4 gate incl. existing inspector/artifact suites | Consumes T4's `workflowRunDrawerLabel`/loading/error copy (T4 precedes); `Loader`/`ErrorState`/`common.close` verified | Sound |
| T6 | S1–S3 author integration tests with explicit RED-vs-guard classification (F6) → S4 verifies the split → S5–S8 implement (focus helpers → header → board composition → drawer) → S9 full suite → S10 gates → S11 scoped commit | Consumes T3 collapsible board, T4 header/copy, T5 drawer — all earlier; `renderView`/`run()`/mocks verified compatible; no new test consumes a later artifact | Sound. `changeView` vs `closeRunDrawer` separation preserves the navigation-focus guard; the drawer's second landmark breaks no existing query (grep-verified) |

No step in Tasks 3–6 runs a test that depends on a file, key, or helper
introduced by a later task, and every claimed RED/pass classification was
re-derived against current source and found truthful.

## 7. Verification matrix

| Behavior | Verifying artifact(s) | Status |
| --- | --- | --- |
| Distinct drawer/inspector landmarks | T5 unit test (plan 1247–1259, real roles via aside-rendering mock); T6 integration (1491–1495, real inspector, distinct names, count 2); acceptance 1847; design 500–501, 551–553 | covered |
| Grid preservation (Kanban page + shared contract) | T3 grid-preservation test (568–579: old classes, no `border-l-2`, origin accepted); amended line-33 assertion (582–589); `layout` defaults to `'grid'` (614–616); checklist 1855 | covered |
| Full-height scrolling chain | T6 S1 class-chain assertions `main → data-workflow-run-view → data-workflow-board-shell → h-full root → flex-1 strip → data-lane-scroll` (1455–1464); T3 S7 assertions (771–782); launch check at 1440px/300 cards (1853) | covered (class-level in jsdom + explicit launch acceptance for computed layout) |
| Virtualization — grid mode | Existing 1,000-card test retained, now on `thousandCardModel()` (784–789; `< 100` gate) | covered |
| Virtualization — lane mode | New lane 1,000-card test under the 600px stub, `< 100` gate (810–827); Step 8 expectation (838–839) | covered |
| Attention focus return | T6 S2 test: card A → Attention B → close → focus on Attention button, not card A (1522–1556); `openRunId` replace semantics (1652–1655); checklist 1850 | covered |
| Background selection invariance | T6 S3 background-refetch guard: refetch with new cards → store stays null, no `complementary` (1608–1624); classified as a pre-existing invariant guard (1629–1630); checklist 1851 | covered |
| RTL header alignment | Arabic header test asserting `ms-auto` / rejecting `ml-auto` on `[data-workflow-run-toolbar]` (1053–1068); design 424–425; checklist 1856 | covered |

## 8. Remaining corrections

No Critical or Important corrections remain. All eight original findings are
closed at the correct task boundaries with credible verification. Two optional
Minor clarifications (no product decision required, both caught by the plan's
own Step 8 gates if left as-is):

1. **R1** — add one sentence to Task 3 Step 5 placing the `collapsibleLanes`
   JSX inside the `collapsible`-guarded branch so the discriminated props
   narrow.
2. **R2** — add one sentence to Task 3 Step 7 stating the width-table test
   keeps its grid render and gains a second collapsible-mode render (or add
   `min-w-0` to the expanded-lane class).

## 9. Command / evidence ledger

All commands ran read-only from the repo root or `apps/desktop`. The only
repository write in this session is this report file. **No test suite, build,
type-check, or state-changing command was executed** — every RED/GREEN and
type conclusion below the state checks is static reasoning from directly-read
source, marked SR. Kind: G = git/state, H = hash, S = source read (Read
tool), X = grep/existence check.

| # | Command (abridged) | Result | Kind |
| --- | --- | --- | --- |
| 1 | `git status --short --branch; git rev-parse --abbrev-ref HEAD; git rev-parse HEAD; git rev-parse origin/base; git worktree list` | branch `base`, HEAD `fb061582f…`, origin/base `786f8dc01…`, 44 worktrees, expected untracked set only | G |
| 2 | `shasum -a 256 <design> <plan> <prior report>` | design `286ffb2b…` ✓, plan `d6492ae7…` ✓ (both match prompt); prior report `cd394ad2…` recorded | H |
| 3 | `git diff --stat 5c9d8a4e6..HEAD; git log --oneline 5c9d8a4e6..HEAD` | one docs commit; only the two immutable inputs changed since the prior review | G |
| 4 | Read (full): revised design (574 lines), revised plan (1861 lines), prior review report (910 lines) | closure targets and finding record | S |
| 5 | Read (full): `activity-board.tsx`, `virtual-card-column.tsx`, `activity-board.test.tsx`, `activity-board.performance.test.tsx`, `types.ts` | current props, card classes at `virtual-card-column.tsx:58`, exact assertion at `activity-board.test.tsx:33`, 600px stub, grid `< 100` gate | S |
| 6 | Read (full): `app/workflows/index.tsx`, `attention-inbox.tsx`, `app/kanban/index.tsx` | current composition (tablist 285–306, `AttentionInbox` wiring 312, below-board inspector 318–325, `onRunLocated` 380–381); attention native button 56–60; Kanban grid consumer 57 | S |
| 7 | `grep aria-label run-inspector.tsx` | inner landmark `` `${run.workflow} run inspector` `` at line 196 | X |
| 8 | Read: `index.test.tsx:1–200` and `870–980`; `grep -n "catches signal and dependency copy"` | `renderView`/mocks/`run()` fixture; locale regression at line 890 with `stringKeys`/`english`/`copy` as the plan assumes | S/X |
| 9 | `grep -n "complementary\|run inspector" index.test.tsx workflow-operations.e2e.test.tsx` | zero hits — no existing complementary-role queries to break | X |
| 10 | `grep -c workflowRunInspectorLabel <design> <plan>` | 0 and 0 — stale key fully removed | X |
| 11 | `grep openWorkflowRun src/i18n/en.ts src/i18n/ar.ts` | en:2129 `` `Open run ${workflow}` ``; absent in ar (deep-merge fallback; tests run in en) | X |
| 12 | `sed src/types/hermes.ts` (health union, `WorkflowAttentionItem` 801–832) | fixture literals (`user_wait`, `approval`, `paused`) valid; all required fields present in the plan's attention fixture | S |
| 13 | `cat activity-board/types.ts`; `command ls src/components/activity-board/` | health union matches `HEALTH_TONE` keys; directory holds exactly the five board files (Task 3 dir-stage is plan-owned) | S/X |
| 14 | `grep use-grab-scroll.ts`; token loop `grep -c -- "--$tok:" styles.css` (13 tokens); `grep loader.tsx` | `{ grabbing, onMouseDown }` exported; all 13 tokens present; `lemniscate-bloom` + `label` present | X |
| 15 | `sed` i18n `operations` block openers (types 1676 / en 2004 / ar 1660 / ja 1836 / zh 2196 / zh-hant 1777); `grep` new keys | every plan-cited insertion range opens at the cited line; `workflowLaneExpand`/`workflowRunDrawerLabel`/`workflowLoadedRunCount` absent today (RED genuine); `workflowViews`/`activeBoard`/`common.close` present | X |
| 16 | `grep disabled search-field.tsx; command ls search-field.test.tsx` | no `disabled` prop today; test file absent as Task 1 claims | X |
| 17 | Read (full): `apps/desktop/AGENTS.md`; `sed DESIGN.md:140–175`; `grep '^## ' AGENTS.md` (root section map + targeted sections) | guidance conformance (focus/background/testing invariants; SearchField bullet at the Task 1 range) | S/X |
| SR | Static reasoning (no execution): all RED/GREEN traces in §4/§6, TypeScript narrowing analysis (R1), width-test ambiguity (R2), landmark-count and focus-sequence traces, button-census for the lane perf gate | derived solely from the sources above | SR |

---

**Final verdict: `READY FOR IMPLEMENTATION`** — F1–F8 all `CLOSED`; no new
Critical or Important correction-caused regression; two optional Minor
clarifications listed in §8.

THE REVIEWED DESIGN AND PLAN ARE READY FOR IMPLEMENTATION.
