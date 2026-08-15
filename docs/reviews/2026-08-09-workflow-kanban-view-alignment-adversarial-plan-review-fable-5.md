# Adversarial design & plan review — Workflow Kanban view alignment (Fable 5)

## 1. Repository state, inputs, environment

| Item | Value |
| --- | --- |
| Repository | `/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent` |
| Branch | `base` (`## base...origin/base [ahead 2]`) |
| `HEAD` | `5c9d8a4e68f2faf4877acc4d08993224704d87de` — matches prompt |
| `origin/base` | `786f8dc0175410044000113233bec2bb610e7733` — matches prompt |
| Worktrees | 44 worktrees listed; none touched |
| Untracked files | Only the pre-existing user-owned `docs/assessments/`, `docs/handoffs/`, Ericsson review artifacts, and the two review prompts — untouched |
| Design SHA-256 | `9282fa07bf464db838566439a0860a9dbd1c3d9bbf3d26861e4022674239d907` — matches prompt |
| Plan SHA-256 | `71e801c654a6840b364d3cbd0ec192983e5cb854253f7b01c7bc594588eb97e1` — matches prompt |
| Reviewer model | Claude Fable 5 (`claude-fable-5`) |
| Platform / date | macOS (Darwin 25.5.0) / 2026-08-09 |

`REVIEW_INPUT_CHANGED` does **not** apply — all hashes and refs match.

Evidence sources reviewed (all read directly from the working tree at the pinned
HEAD): both immutable inputs in full; `AGENTS.md` (root, scoped skim of process
sections plus full desktop guide), `apps/desktop/AGENTS.md`,
`apps/desktop/DESIGN.md` (form-control and layout sections incl. lines 140–175);
all six SDK Kanban reference files (targeted reads of `board.tsx` lane/collapse/
strip/header regions, `drawer.tsx` shell/Escape, full `kanban.css`); all eight
shared-board/Kanban-consumer files in full; all eleven Workflows files (full
reads of `index.tsx`, `run-inspector.tsx`, `store.ts`, `detail-query.ts`,
`adapter.ts`, `attention-inbox.tsx`, `workflow-operations.e2e.test.tsx`;
targeted reads of `index.test.tsx` covering the helper block, the locale
regression test, and every behavior-sensitive suite section); all sixteen
primitive/convention files (full reads of `search-field.tsx`, `button.tsx`,
`error-state.tsx`, `tabs.tsx`, `use-grab-scroll.ts`, `eslint.config.mjs`,
`runtime.test.ts`; targeted reads of `loader.tsx`/`loader-native.tsx`,
`persisted.ts` via `store.ts`, `package.json` scripts, five locale catalogs,
`types.ts`); `src/i18n/define-locale.ts`, `src/i18n/context.tsx`,
`src/app/contrib/surfaces.tsx`, `src/types/hermes.ts`,
`src/app/kanban/{index,adapter,task-inspector,index.test,kanban-operations.e2e.test}`;
installed Radix sources (`@radix-ui/react-dismissable-layer`,
`react-use-escape-keydown`); `@vscode/codicons`; git history for the activity
board, Workflows view, Kanban plugin, and the workflow store. Exact commands are
in §18.

## 2. Overall verdict

**`BLOCK`** — five Important findings (two make the written TDD sequence
non-executable as specified; one breaks lane virtualization in production; one
ships wrong focus-return behavior; one demonstrably violates the shared-board
invariant). No Critical finding: no false architectural premise requires a
redesign — the selected Approach A, the pure lane-state algorithm, the query
projection, and the Escape model all survived attack.

## 3. Findings

### Findings table (severity order)

| ID | Sev | Title | Surface |
| --- | --- | --- | --- |
| F1 | IMPORTANT | Drawer accessible name duplicates `RunInspector`'s landmark — Task 6's key integration test cannot pass; duplicate nested `complementary` landmarks | `workflow-run-drawer.tsx`, i18n, `index.test.tsx` |
| F2 | IMPORTANT | New `onOpenCard(card, origin)` second argument breaks the untouched existing grid test assertion — Task 3 GREEN gate fails as written | `virtual-card-column.tsx`, `activity-board.test.tsx` |
| F3 | IMPORTANT | The planned flex/overflow chain never bounds lane bodies — per-lane scrolling and large-lane virtualization silently break in production | `index.tsx` Step 7 region, `virtual-card-column.tsx` lane body |
| F4 | IMPORTANT | Attention-inbox activation leaves a stale focus-return origin — closing after an Attention-driven run switch focuses the wrong card | `index.tsx` (`openRun`/`AttentionInbox` wiring) |
| F5 | IMPORTANT | Unconditional shared-card restyle changes the existing Kanban page's rendering — a demonstrated invariant-2 violation with no task, test, or decision covering it | `virtual-card-column.tsx` Step 6, Kanban page |
| F6 | MINOR | Several predicted REDs are wrong: locale keys are written before their regression runs; two Task 6 tests pass before implementation | Tasks 4 & 6 |
| F7 | MINOR | `activity-board.performance.test.tsx` is listed as Modified with no modifying step, and no lane-appearance large-column coverage exists anywhere | Task 3 |
| F8 | MINOR | Header uses physical `ml-auto`/`mr-2` while the app flips `document.documentElement.dir` to `rtl` for Arabic — toolbar loses its end anchor in RTL | `workflow-view-header.tsx` |

---

### F1 — IMPORTANT — Drawer landmark name duplicates `RunInspector`'s

1. **ID/severity:** F1, Important.
2. **Title:** The drawer's `aria-label` (`workflowRunInspectorLabel` = en
   `` `${workflow} run inspector` ``) is byte-identical to `RunInspector`'s own
   root `<aside aria-label={`${run.workflow} run inspector`}>`, producing two
   nested `complementary` landmarks with the same accessible name.
3. **Affected:** Design §WorkflowRunDrawer + §Accessibility ("named
   complementary region"); Plan Task 4 Step 2 (`workflowRunInspectorLabel`),
   Task 5 Step 3 (drawer `<aside aria-label={label}>`), Task 6 Step 2 test;
   production surface: Workflows run drawer.
4. **Evidence:** Plan Task 5 Step 3 renders
   `<aside aria-label={copy.workflowRunInspectorLabel(run?.workflow ?? selectedRunId)}>`
   wrapping `<RunInspector …>`. `run-inspector.tsx:196` (unchangeable per the
   plan's own constraint) is
   `<aside aria-label={`${run.workflow} run inspector`} …>`. Task 4 Step 2 sets
   en `workflowRunInspectorLabel: workflow => `${workflow} run inspector``. With
   run `workflow: 'Laptop diagnostic'` both asides answer to
   `'Laptop diagnostic run inspector'`. Task 6 Step 2's test then runs
   `await screen.findByRole('complementary', { name: 'Laptop diagnostic run inspector' })`.
   Testing Library `findByRole` is `getByRole` + `waitFor`, and `getByRole`
   throws on multiple matches. During loading the outer label is
   `'run-1 run inspector'` (falls back to `selectedRunId`), so the query only
   starts matching once detail loads — at which point **both** asides match.
5. **Violated invariant:** #16 (plan executability — the GREEN step cannot make
   this test pass) and #14 (truthful native semantics — two nested
   `complementary` landmarks with an identical name is a landmark-navigation
   defect for screen-reader users).
6. **Scenario:** Implementer follows Tasks 4–6 exactly; Task 6 Step 9's suite
   run reaches `opens selected run detail in a side drawer…`; the query rejects
   with "Found multiple elements with the role complementary…".
7. **Wrong result / consequence:** The integration gate can never go green
   without ad-hoc deviation; if the assertion is "fixed" by loosening the query
   instead of the markup, the shipped DOM carries duplicate same-named nested
   complementary landmarks — screen-reader landmark lists show two
   indistinguishable "Laptop diagnostic run inspector" regions, one inside the
   other.
8. **Why nothing else covers it:** Task 5's drawer unit test mocks
   `./run-inspector` with a plain `<div>`, so the collision is invisible there;
   no other task renders the real inspector inside the drawer and queries by
   landmark name.
9. **Smallest correction:** Give the drawer a distinct localized name — change
   `workflowRunInspectorLabel` in all five locales to a non-colliding phrase
   (e.g. en `` `${workflow} run details` ``), keep the drawer as the single
   *outer* complementary region, and scope Task 6 Step 2's inspector-tab
   queries with `within(drawer)` (already done). No `RunInspector` change
   needed.
10. **RED/verification to add:** In `workflow-run-drawer.test.tsx`, un-mock the
    inspector for one case (or add an integration assertion) that
    `screen.getAllByRole('complementary')` yields regions with **distinct**
    accessible names when a run is loaded; keep Task 6 Step 2 querying the
    corrected drawer name.

### F2 — IMPORTANT — `origin` argument breaks the existing grid assertion

1. **ID/severity:** F2, Important.
2. **Title:** Task 3 Step 6 changes the shared card `onClick` to
   `onOpenCard(card, event.currentTarget)` for **both** appearances, but the
   plan never updates `activity-board.test.tsx`'s existing exact-argument
   assertion, so Task 3 Step 8's "all shared board tests pass" is false.
3. **Affected:** Plan Task 3 Steps 6/8; design §ActivityBoard
   (`onOpenCard(card, origin?)`); production surface: shared board (both
   consumers).
4. **Evidence:** `activity-board.test.tsx:33` (current, in the file Task 3
   modifies):
   `expect(open).toHaveBeenCalledWith(model.columns[0]!.cards[0])`. Vitest's
   `toHaveBeenCalledWith` requires the full argument list to match; after
   Step 6 the mock is called with `(card, HTMLButtonElement)` in grid mode too
   (the card renderer is shared). Task 3 Steps 1–2 only *add* tests; no step
   amends line 33. Step 8's expectation: "all shared board tests pass".
5. **Violated invariant:** #16 (every task predicts the correct failure and the
   GREEN gate is achievable as written); also directly falsifies premise 3
   ("safe for every current caller **and test double**").
6. **Scenario:** Implementer completes Task 3 Steps 4–6 exactly, runs Step 8;
   `opens cards with keyboard-compatible native controls and loads bounded
   pages` fails on argument mismatch.
7. **Wrong result / consequence:** The task's exit gate fails on a test the plan
   forbade thinking about; the likely improvised "fix" (only passing `origin`
   in lane mode, or loosening the assertion) is an undesigned decision made
   mid-implementation.
8. **Why nothing else covers it:** The plan's new Step 2 test asserts the
   two-argument call for lane mode but nothing reconciles the pre-existing
   one-argument grid assertion; runtime callers (`KanbanView`, `WorkflowsView`)
   are variance-safe, so only this test trips.
9. **Smallest correction:** Add to Task 3 Step 1/2 an explicit edit of the
   existing assertion to
   `expect(open).toHaveBeenCalledWith(model.columns[0]!.cards[0], expect.any(HTMLButtonElement))`
   (or assert `open.mock.calls[0]![0]`), stating that grid mode now also
   supplies the origin.
10. **RED/verification:** The amended assertion itself is the verification; it
    fails before Step 6 (single-argument call) and passes after — a correct
    RED→GREEN pair the current plan lacks.

### F3 — IMPORTANT — No definite height ever reaches the lane bodies

1. **ID/severity:** F3, Important.
2. **Title:** The plan's chain — page root `h-full … overflow-hidden` → content
   region `min-h-0 flex-1 overflow-y-auto` (Attention + board + cleanup
   stacked) → lane strip `flex … overflow-x-auto` (no `flex-1`/height) → lane
   `h-full w-64` → body `min-h-0 flex-1` — leaves the strip auto-height, so
   `h-full` on lanes resolves against an indefinite parent and lane bodies grow
   to content height: no per-lane scrolling, and the virtualizer's scroll
   element becomes as tall as its content.
3. **Affected:** Design §ActivityBoard ("full available board height"),
   §Responsive; Plan Task 3 Step 5 (strip classes) + Step 6 ("in lane
   appearance it uses `min-h-0 flex-1`") + Task 6 Steps 6–7 (page root and
   `min-h-0 flex-1 overflow-y-auto` wrapper); production surface: all three run
   views.
4. **Evidence:** Task 6 Step 7: "Wrap Attention inbox, board, and cleanup
   sections in one `min-h-0 flex-1 overflow-y-auto` content region." Task 3
   Step 5's strip: `'flex min-h-0 min-w-0 gap-2 overflow-x-auto
   overscroll-contain'` — no height/flex growth, and the `ActivityBoard` root
   div (`min-w-0`) contributes none either. CSS: a percentage height (`h-full`)
   against an auto-height flex item is indefinite → auto; `flex-1 min-h-0` in an
   auto-height column also resolves to content height. Contrast the SDK
   reference the design cites: `board.tsx:1390` puts the strip **directly** in
   the page column as `flex flex-1 gap-2 overflow-x-auto` under
   `relative flex h-full flex-col overflow-hidden` (`board.tsx:1382`) — the
   strip is the flex-grown, height-bounded row; there is no interposed
   vertically-scrolling stack. `virtual-card-column.tsx:19-25` shows the
   virtualizer keys off `getScrollElement: () => parent.current` — if `parent`
   never overflows, `getVirtualItems()` covers the whole content and the >50
   virtualization path degenerates to mounting every row (the grid mode's
   `max-h-[65dvh]` bound, which currently prevents exactly this, is removed in
   lane appearance).
5. **Violated invariant:** #12 ("Large lanes remain virtualized; horizontal
   overflow stays inside the lane strip") and the design's "full available
   board height" / "Keep large run collections virtualized and bounded".
6. **Scenario:** History view with 300+ loaded runs in Completed; production
   window. The lane body is ~25,000 px tall, the whole content region scrolls
   as one page, every card mounts, and lane headers scroll out of view.
7. **Wrong result / consequence:** Long-lane views regress to unvirtualized
   full-page scrolling — exactly the perf/containment behavior the redesign
   exists to fix; the drawer alongside a 25,000 px board is also visually
   wrong (the design promises independently scrolling lanes beside the drawer).
8. **Why no gate catches it:** jsdom has no layout: Task 3 Step 7 asserts only
   class fragments (`overflow-x-auto`, `min-w-0`); the 1,000-card performance
   test stubs `getBoundingClientRect` to 600 px and renders **grid** mode, so
   it passes regardless; type-check/lint are indifferent.
9. **Smallest correction:** In Task 6 Step 7, make the run-view content region a
   **non-scrolling** `min-h-0 flex-1 flex flex-col`; render Attention and the
   cleanup section as `shrink-0` rows and give the board wrapper
   `min-h-0 flex-1`; in Task 3 give the lane strip `min-h-0 flex-1` (mirroring
   `board.tsx:1390`) so `h-full` lanes and `min-h-0 flex-1` bodies resolve. If
   the design's "run views render … cleanup … inside the main scrollable
   content region" sentence is read as one shared scroll surface instead, the
   design must be corrected first — the two sentences cannot both hold.
10. **RED/verification to add:** Extend Task 3 Step 7 to assert the full class
    chain (strip contains `flex-1 min-h-0`; lane body element — reachable via
    `data-column` — carries `overflow-y-auto`), extend the performance test (or
    add a sibling) to render `layout="collapsible-lanes"` with the existing
    600 px rect stub and assert `< 100` buttons, and add an acceptance-checklist
    line "a 300-card lane scrolls inside the lane at 1440 px with the page not
    scrolling vertically" for the launch check.

### F4 — IMPORTANT — Attention activation leaves a stale focus-return origin

1. **ID/severity:** F4, Important.
2. **Title:** `AttentionInbox` keeps `onOpenRun={selectWorkflowRun}` (untouched
   by the plan), so switching to another run from Attention while the drawer is
   open leaves `runReturnFocusRef` pointing at the previously activated board
   card; closing then focuses that unrelated card.
3. **Affected:** Design §WorkflowRunDrawer ("Closing returns focus to that card
   when it is still connected; otherwise … heading"), §Accessibility; Plan
   Task 6 Step 5 (`openRun`/`closeRunDrawer`) and Step 7 (composition keeps
   `<AttentionInbox … onOpenRun={selectWorkflowRun} />` unchanged); production
   surface: Attention inbox → drawer.
4. **Evidence:** `index.tsx:312` today:
   `<AttentionInbox items={…} onOpenRun={selectWorkflowRun} />`. Plan Task 6
   Step 5 sets the ref **only** in `openRun` (the `ActivityBoard` path) and
   clears it only in `closeRunDrawer`/`changeView`. Sequence: card A click →
   `runReturnFocusRef.current = cardA`; Attention item for run B click →
   `selectWorkflowRun('B')` (ref untouched, drawer switches to B); close →
   `target = cardA`, `cardA.isConnected` true → `cardA.focus()`.
5. **Violated invariant:** #8/#11 ("Every user-owned run-opening surface has
   deterministic selection **and focus-return** behavior"; "closing returns
   focus to the activation origin when connected and otherwise to a deliberate
   fallback") — card A is neither the activation origin of the run-B drawer nor
   a deliberate fallback. Premise 14 is falsified for the Attention route.
6. **Scenario:** Keyboard user opens run A from the board, notices an Attention
   row for run B, activates it, resolves it, presses Escape — focus jumps to
   card A elsewhere on the board rather than the Attention region they were
   working in.
7. **Wrong result / consequence:** Focus lands on an element the user never
   intended to return to; for screen-reader users this reads as an unexplained
   context teleport. It ships silently — behavior is "deterministic" enough to
   pass every planned test.
8. **Why nothing else covers it:** Task 6's focus tests only exercise the
   card-origin and view-change routes; no test opens the drawer via Attention,
   and the drawer unit test takes `onClose` as an opaque mock.
9. **Smallest correction:** In Task 6 Step 5, route Attention through a wrapper
   that re-homes the origin, e.g.
   `onOpenRun={runId => { runReturnFocusRef.current = document.activeElement instanceof HTMLButtonElement ? document.activeElement : null; selectWorkflowRun(runId) }}`
   (the Attention row *is* a native button — `attention-inbox.tsx:56`), so close
   returns focus to the Attention row, with the heading fallback intact.
10. **RED/verification to add:** Integration test: open run A from its card,
    click `Open run Approval workflow` (existing Attention fixture), close the
    drawer, assert `document.activeElement` is the Attention button (or at
    minimum **not** card A).

### F5 — IMPORTANT — Shared-card restyle silently changes the Kanban page

1. **ID/severity:** F5, Important (resolvable by an explicit product decision).
2. **Title:** Task 3 Step 6 rewrites the shared card button (elevated surface,
   tertiary hairline, 2 px health accent, new hover/selected chrome, badge tone
   classes) unconditionally, so the existing Kanban page's grid cards no longer
   render as before.
3. **Affected:** Non-negotiable invariant 2; design §Approaches A ("without
   forcing it to adopt the mode in this phase") vs §Workflow cards; production
   surface: `src/app/kanban/index.tsx` fallback page.
4. **Evidence:** Current card (`virtual-card-column.tsx:58`):
   `'block w-full rounded-sm bg-(--ui-bg-quaternary) p-3 text-left …'`. Plan
   Step 6 replaces it ("Update each card button to:") with
   `rounded-md border border-(--ui-stroke-tertiary) border-l-2 bg-(--ui-bg-elevated) p-2.5 …`
   plus `style={{ borderLeftColor: HEALTH_TONE[card.health] }}` — in the shared
   renderer used by **both** appearances. `KanbanView`
   (`src/app/kanban/index.tsx:57`) renders default-grid `ActivityBoard`;
   `kanbanBoardModel` maps `blocked→attention`, `done→terminal`, else
   `healthy`, so Kanban tasks acquire yellow/gray/green left accents they never
   had. No plan task, test, or note mentions the Kanban page's appearance; the
   Kanban tests (`index.test.tsx` smoke, `kanban-operations.e2e.test.tsx`)
   assert no classes, so nothing fails.
5. **Violated invariant:** #2 — "the existing Kanban page **renders**, selects
   tasks, paginates, and virtualizes exactly as before." Behavior is preserved;
   rendering is not. The prompt states a demonstrated violation is Critical or
   Important by blast radius; behavior-preserving visual drift on a secondary
   surface → Important, not Critical.
6. **Scenario:** Ship the plan; a user opens the built-in Kanban page and every
   task card has new chrome and per-status color accents that no design review
   for that surface approved.
7. **Wrong result / consequence:** An unreviewed visual change on a surface the
   plan promised to leave alone; if upstream later restyles the plugin-parity
   fallback deliberately, the provenance of this drift is untracked.
8. **Why nothing else covers it:** Invariant 2's test proxy
   (`test:workflow-ui` includes `src/app/kanban/`) only asserts behavior;
   Task 3's "Keep the existing grid JSX byte-for-byte" sentence covers the
   `ActivityBoard` branch, not the shared card renderer below it.
9. **Smallest correction:** Either (a) gate the new card chrome on
   `appearance === 'lane'` and keep the grid card byte-identical, or (b) record
   the explicit product decision that the Kanban grid adopts the new card
   treatment in this phase and amend invariant 2's wording ("behaviorally
   unchanged") plus a one-line Kanban-page note in the plan. (a) is the
   no-decision path.
10. **RED/verification to add:** If (a): a grid-mode test asserting the card
    retains `bg-(--ui-bg-quaternary)` and gains no `border-l-2`; if (b): the
    decision recorded in the design doc and the invariant text updated.

### F6 — MINOR — Wrong RED predictions in Tasks 4 and 6

1. **ID/severity:** F6, Minor.
2. **Title:** Three RED steps don't fail as predicted: Task 4 writes the locale
   keys (Steps 1–2) and extends the regression (Step 3) *before* the Step 5
   "verify failure" run, so the locale half never REDs and Step 5's stated
   reason ("the localization keys do not exist") is false at that point;
   Task 6 Step 1's catalog-guard test and Step 3's navigation-focus test pass
   against the current implementation.
3. **Affected:** Plan Task 4 Steps 1–5; Task 6 Step 1 (`keeps the catalog free
   of run-only toolbar controls`), Step 3 (`closes selected detail without
   stealing focus…`), Step 4's blanket "new tests fail" expectation.
4. **Evidence:** Task 4 step order as written; current `index.tsx:290-293`
   already clears selection on tab click and the current below-board
   `RunInspector` aside satisfies "complementary appears then disappears", so
   Step 3's assertions (`selectedRunId` null, no complementary, focus retained
   on the manually-focused tab) all hold pre-change; the catalog test queries
   controls that don't exist yet and asserts null.
5. **Violated invariant:** #16 ("writes RED before GREEN, predicts the correct
   failure").
6. **Scenario:** An implementer running strict RED discipline stops at Task 4
   Step 5 / Task 6 Step 4 because observed results contradict the plan.
7. **Consequence:** Lost time and improvised judgment; guard tests that never
   REDed prove less than the plan implies.
8. **Why not covered elsewhere:** The final gates still run these tests; only
   the RED discipline claim is wrong.
9. **Smallest correction:** Reorder Task 4 (write the header test + regression
   extension, run RED, then add keys/translations), and relabel the two Task 6
   tests as regression guards expected to pass before and after.
10. **Verification:** The reordered Task 4 RED run failing on missing keys with
    `not.toBe(english[...])` unreachable; plan text updated for Task 6.

### F7 — MINOR — Performance test listed but never modified; no lane-mode perf coverage

1. **ID/severity:** F7, Minor (compounds F3).
2. **Title:** Task 3's file list marks
   `activity-board.performance.test.tsx:1-58` as Modify, but no step touches
   it, and no task anywhere exercises the >50-card virtualization contract in
   `collapsible-lanes` mode.
3. **Affected:** Plan Task 3 file list / Steps 7–8; invariant 12's verification
   path.
4. **Evidence:** The performance test renders default-grid `ActivityBoard`
   (lines 30–54) and is untouched by Steps 1–7; Step 8 merely runs it.
5. **Violated invariant:** #16 (file list vs steps mismatch); weakens #12's
   only credible gate.
6. **Scenario:** Implementer searches Task 3 for the promised performance-test
   change and finds none; lane-mode virtualization ships gated only by a
   grid-mode test.
7. **Consequence:** Ambiguity now; untested release-critical behavior later
   (see F3 for the behavior itself).
8. **Why not covered elsewhere:** No other file exercises 1,000-card columns.
9. **Smallest correction:** Either drop the file from the Modify list or (with
   F3's fix) add the lane-appearance 1,000-card case there.
10. **Verification:** Lane-mode perf case asserting `< 100` mounted buttons
    under the existing 600 px rect stub.

### F8 — MINOR — Physical `ml-auto`/`mr-2` in an RTL-switching app

1. **ID/severity:** F8, Minor.
2. **Title:** `WorkflowViewHeader` anchors the count/filter/search cluster with
   `ml-auto` (and offsets the heading with `mr-2`) — physical properties — while
   `src/i18n/context.tsx:66` sets `document.documentElement.dir = 'rtl'` for
   Arabic, so in ar the cluster loses its end anchor (its auto margin sits on
   the side where RTL packing already leaves the free space).
3. **Affected:** Plan Task 4 Step 6; design §Responsive/§Accessibility (header
   wraps correctly); review scope item 8 (RTL).
4. **Evidence:** Plan snippet `className="ml-auto flex min-w-0 items-center gap-1.5"`;
   `context.tsx:66`; the codebase already uses logical `ms-*` utilities
   elsewhere (e.g. `src/app/settings/gateway-settings.tsx`).
5. **Violated invariant:** #14 (localization completeness includes RTL
   behavior; the design's own testing scope lists RTL).
6. **Scenario:** Arabic locale, run view: the toolbar renders adjacent to the
   nav tabs instead of at the line's logical end; heading spacing is also on
   the wrong side.
7. **Consequence:** Broken header hierarchy in one shipped locale; invisible to
   every planned test (all run under `initialLocale="en"` with LTR jsdom).
8. **Why not covered elsewhere:** No RTL rendering test exists in the plan.
9. **Smallest correction:** Use `ms-auto` and `me-2` in the Step 6 snippet.
10. **Verification:** Either a snapshot-free assertion that the cluster carries
    `ms-auto`, or an acceptance-checklist line for an ar-locale visual check.

## 4. Design-requirement → plan traceability matrix

| # | Design requirement (normative) | Plan tasks / tests | Rating |
| --- | --- | --- | --- |
| D1 | Horizontal strip of fixed 16rem lanes, 2rem rails, contained horizontal scroll | T3 S5–S7; strip/rail classes; width-table test | **partial** — geometry present; height chain broken (F3) |
| D2 | Empty lanes auto-collapse only when board has work; all-empty stays expanded | T2 S2/S4 (`laneIsCollapsed`), T3 S1 tests | complete |
| D3 | Empty expanded lane shows localized "No runs" | T3 S6 (`emptyLabel`), T4 keys, T3 tests | complete |
| D4 | Keyboard-operable expand/collapse with `aria-expanded`, localized names | T3 S1/S6 | complete |
| D5 | Overrides scoped to phase + `collapseScope`; view change resets | T2 S4, T3 S1 scope test, T6 S7 (`collapseScope={view}`) | complete |
| D6 | Lane state in-memory, never persisted | T2/T3 (`useState`, no storage) | complete |
| D7 | Card treatment: elevated surface, hairline, 2 px health accent, tokens only, no drag | T3 S6 `HEALTH_TONE` (all vars verified in `styles.css`) | complete for Workflows — but leaks to Kanban grid (F5) |
| D8 | Virtualization > 50 cards; focus survives reorder | existing tests retained; T3 S8 | **partial** — lane-mode virtualization unproven and broken (F3/F7) |
| D9 | Compact header: heading, nav, count badge (run views only), disabled filter + search; catalog exempt | T4 S6, T6 S1 tests | complete |
| D10 | Disabled controls native-disabled, localized, no state/query/network | T1, T4 S6 (`disabled`, no-op `onChange`) | complete |
| D11 | `SearchField.disabled` + DESIGN.md documentation | T1 S3/S4 (verified against current `search-field.tsx` structure incl. `effectivePlaceholder`) | complete |
| D12 | Drawer: right-side, nonmodal, 32rem/full-width narrow, elevated, left hairline, close button, no tooltip | T5 S3 | complete (name defect F1) |
| D13 | Drawer loading (Loader) / error (ErrorState) / stale-data-wins; closes on Escape exactly once | T5 S1/S3 (Radix `preventDefault` verified in installed source) | complete |
| D14 | `RunInspector` unchanged; keyed by `run.run_id`; tab resets to Overview | T5 S3 (`key={run.run_id}`), T6 S3 test | complete |
| D15 | Focus return to originating card, else heading | T6 S5; **Attention route unhandled** | **partial** (F4) |
| D16 | View change clears selection before render, closes drawer, stops polling | T6 S5 `changeView`; existing cancellation effect kept | complete |
| D17 | Background events never select/open | structural (no polling path calls `selectWorkflowRun`); design's "background query updates never select" test **absent from the plan** | **partial** — code-inspection guarantee only |
| D18 | All new copy in 5 locales; no literal production strings | T4 S1–S3 (verified `defineLocale` deep-merge makes the `not.toBe(english…)` guard sound) | complete |
| D19 | No page-level horizontal overflow at 320/768/1440 | T3 S7 | **partial** — class-fragment assertions only (jsdom limit; noted, not a finding) |
| D20 | Reduced-motion-safe transitions | T3 S6/S7 (`motion-reduce:transition-none`), drawer has no animation (deliberately drops SDK `animate-in`) | complete |
| D21 | Attention above board; cleanup preview/execute semantics unchanged | T6 S7; existing cleanup tests retained | complete |
| D22 | Evidence queries, actions, CAS, 409 recovery unchanged | T6 S8 keeps expressions verbatim (matched against `index.tsx:318-325`); e2e suite retained | complete |
| D23 | SearchField test: "input cannot emit `onChange`" | T1 test asserts disabled + no trailing action but never attempts an input | **partial** (minor; add a `fireEvent`-based non-emission or type-level note) |

Orphan design requirements: D17's dedicated test. Plan work without design
authority: none found (every task traces to a design section).

## 5. Task coverage matrix (Tasks 1–6)

| Task | Files exist / new-file ownership | RED honest? | GREEN complete & type-consistent? | Gate achievable as written? | Rating |
| --- | --- | --- | --- | --- | --- |
| T1 SearchField | ✔ (`search-field.tsx` verified line-accurate; test file absent as claimed; `no-native-title.test.ts` exists; DESIGN.md bullet at cited range) | ✔ (unknown prop → not disabled → fails as stated) | ✔ (fragments match the real component incl. clear-button/Tip/`t.ui.search.clear`) | ✔ | complete |
| T2 lane-collapse | ✔ new files in the right package; `ActivityBoardColumn` import correct | ✔ (module missing) | ✔ (all 5 tests hand-traced green against the given implementation, incl. reference-identity) | ✔ | complete |
| T3 board rendering | ✔ ranges accurate; `useGrabScroll`/`cn` exist; tokens verified; TS aliased-discriminant narrowing valid | ✔ for the 4 new tests | GREEN breaks an untouched existing test (F2); height chain unspecified (F3); perf file listed, never edited (F7) | ✘ Step 8 fails | **contradicted** |
| T4 header + i18n | ✔ all six locale ranges byte-accurate (`operations` at types:1676 / en:2004 / ar:1660 / ja:1836 / zh:2196 / zh-hant:1777); regression test found at `index.test.tsx:890` with `stringKeys`/`english`/`copy` exactly as described; `Codicon name="filter"` precedented at `board.tsx:900`; `Button` sizes/variants verified | locale half never RED (F6) | ✔ (`WorkflowRunView` includes `'workflows'`; `workflowViews` key exists; header mirrors the current tablist markup) | ✔ | partial |
| T5 drawer | ✔ new files; `Loader` accepts `label` + `lemniscate-bloom` (verified in `loader.tsx`/`loader-native.tsx:100`); `ErrorState` props verified; `t.common.close` = 'Close'; `WorkflowRunSnapshot` fixture type-checked against `hermes.ts` (all required fields present, `status: 'running'` valid) | ✔ | ✔ unit-level; the shell's landmark name collides at integration (F1) | ✔ unit / ✘ downstream | partial |
| T6 integration | ✔ ranges accurate; `renderView` helper's selection-restore mechanics verified (`index.test.tsx:119-142`) — the plan's tests are consistent with it; `mouseDown` tab activation matches existing Radix usage; mocks (`getWorkflowRun` etc.) exist | 2 of 7 not RED (F6) | Step 2's landmark query cannot pass (F1); Attention focus-origin unwired (F4); layout region under-specified (F3) | ✘ Step 9 fails | **contradicted** |

Ordering: no test or GREEN step consumes a file created by a later task
(T1→T4→T6 and T2→T3→T6 dependencies are correctly sequenced; Task 3's Workflows
consumer keeps compiling between T3 and T6 because grid stays the default and
the widened callback is variance-compatible).

## 6. SDK-Kanban-reference parity/adaptation matrix

| Aspect | SDK Kanban source | Plan | Judgment |
| --- | --- | --- | --- |
| Expanded lane width | `w-64` (`board.tsx:444`) | `w-64` | parity |
| Collapsed rail | `w-8` full-height button, dot, `[writing-mode:vertical-rl]` label, count only when nonzero (`board.tsx:416-437`) | 2rem button in a named `<section>`, same content rules | parity + deliberate adaptation (section wrapper preserves the board's named-region contract; adds `aria-expanded` the SDK lacks — improvement) |
| Auto-collapse rule | `auto = boardHasWork && col.tasks.length === 0` (`board.tsx:1395`) | `boardHasCards && column.cards.length === 0` | parity |
| All-empty board | auto disabled — "a wall of rails teaches nothing" (`board.tsx:1258`) | identical rule + localized `No runs` body | parity |
| Override lifetime | deviations-only map; dropped on empty/occupied phase flip (`board.tsx:1264-1319`) | `reconcileLaneCollapseState` phase comparison — equivalent, plus removed-lane pruning | parity (algorithm re-derived, not copied; honors the no-plugin-import rule) |
| Override persistence | `$collapsedLanes` persisted via plugin storage (`api.ts:49,100`) | in-memory, scope-keyed, reset on remount | **approved adaptation** (design explicitly excludes the plugin storage contract) |
| Strip + grab scroll | `flex flex-1 gap-2 overflow-x-auto` + `useGrabScroll` + `cursor-grabbing` (`board.tsx:1390`) | same hook and classes, **minus `flex-1`** | parity except the omitted height growth — that omission is F3 |
| Header hierarchy | title + count + filter (`Codicon name="filter"`, 0.85rem) + search | heading + nav + count badge + disabled filter (same icon/size) + disabled `SearchField` | parity; disabled-not-functional is the approved scope cut |
| Card treatment | elevated card, hairline, tone accents (inline `meta.tone`) | token-only `HEALTH_TONE` CSS vars | parity via adaptation (tokens instead of literals — required by design) |
| Drawer shell | `absolute inset-y-0 right-0 z-20 w-[26rem] border-l border-(--ui-stroke-tertiary) bg-(--ui-bg-elevated)` + `animate-in slide-in-from-right-4` (`drawer.tsx:735`) | same anchoring, `32rem`/full-width-narrow, **no entry animation** | deliberate adaptations: 32rem is design-mandated for inspector content; dropping `animate-in` sidesteps the SDK's non-reduced-motion-guarded animation |
| Drawer Escape | bare `event.key === 'Escape' && onClose()` (`drawer.tsx:638`) | window listener guarded by `defaultPrevented`, `preventDefault` on consume | improvement over reference; verified compatible with Radix (see §12) |
| Drag/drop, bulk select, task creation, assignee lanes, profile filters | present throughout `board.tsx` | correctly excluded | approved scope exclusion — not demanded anywhere in the plan |

No accidental divergence found beyond F3's missing `flex-1`.

## 7. Shared-`ActivityBoard` consumer compatibility matrix

| Consumer | Current contract | Under the plan | Verdict |
| --- | --- | --- | --- |
| `WorkflowsView` (`src/app/workflows/index.tsx:313-317`) | grid, `onOpenCard: card => selectWorkflowRun(card.id)` | T6 switches to collapsible + `openRun(card, origin)` | compatible (intended change) |
| `KanbanView` (`src/app/kanban/index.tsx:57`) | grid, `onOpenCard: card => selectKanbanTask(card.id)`; count from server `column_counts` | no prop change needed; discriminated union keeps grid default; widened callback is variance-safe; `selectedCardId` absent → cards get no `aria-expanded` (verified conditional) | type/behavior compatible; **rendering changes via the shared card restyle (F5)** |
| `activity-board.test.tsx` | `toHaveBeenCalledWith(card)` exact-args (line 33) | called with `(card, button)` | **breaks (F2)** |
| `activity-board.performance.test.tsx` | grid, 600 px rect stub | unchanged | passes, but proves nothing about lane mode (F7) |

Card keys (`card.id`), focus-under-reorder (existing test retained), bounded
`Load more` (`nextCursor` gate untouched), stale `role="status"` line, badge
tones (currently ignored; plan starts honoring `tone` — additive), and the
1,000-card contract (grid) were each traced and remain intact.
`useGrabScroll` coexistence: verified safe — `BLOCKED_TARGETS`
(`use-grab-scroll.ts:18,41`) exempts buttons/inputs/links, so card, rail, and
Load-more clicks never start a pan; the hook only pans the strip element it is
bound to, so nested lane vertical scrolling is unaffected; the scrollbar-gutter
guard preserves native scrollbar dragging.

## 8. Workflows state/query/action preservation matrix

| Seam | Current (verified) | Plan | Verdict |
| --- | --- | --- | --- |
| Run list | `useInfiniteQuery(['workflow-runs', profile, view])`, `loadWorkflowRunPage(view, cursor)` → `listWorkflowRuns(cursor, view)`, 20 s visible-only interval | untouched; `onLoadMore` still `fetchNextPage` | preserved |
| Attention | `['workflow-attention', profile]`, 20 s | untouched | preserved |
| Selected run | `['workflow-run', profile, selectedRunId]`, enabled `view !== 'workflows' && Boolean(selectedRunId)`, 20 s | projected into drawer props (`selected.data/error/isLoading`); key/enablement/interval verbatim | preserved |
| Events | append-merge queryFn, 1 s visible interval, cancellation effect on hide/deselect/unmount | untouched; drawer receives `events.data?.events` | preserved (closing sets `selectedRunId` null → existing effect cancels) |
| Mutation/CAS/409 | `mutateWorkflowRun` with `expected_version`/`interaction_id`; 409 → refetch + 3 invalidations; `actionInFlight` guard | `mutateRun` and `actionsDisabled` expression passed through verbatim (T6 S8 matches `index.tsx:318-325` exactly) | preserved |
| Cleanup | preview-token → execute mutations, History/Archive only | JSX preserved per T6 S7 | preserved |
| Selected-run identity | persisted nanostore `$workflowSelectedRunId` (`store.ts:3-5`); view starts at `'workflows'` and every navigation clears selection, so a persisted id never auto-opens the drawer on mount in production | `changeView`/`closeRunDrawer` clear it; no new writer | preserved |
| Profile routing | `getApiRequestProfile()` in every key | untouched | preserved |
| Drawer-open routes | card click (`openRun`), Attention click, `ReviewRunDialog.onRunLocated` (`selectWorkflowRun(runId); setView('board')` — a foreground user action reachable only from the catalog, where `changeView` has already nulled the ref) | all reach the drawer via the same store; only `openRun` manages the focus ref | preserved except the Attention focus-origin gap (F4) |
| Loaded count | n/a today | `model.columns.reduce(cards.length)`; adapter maps each run to exactly one column (`columnId` is a single deterministic branch), `runItems` is the flat page list → count ≡ loaded runs, no double count, correct across pagination | honest |

## 9. Sixteen non-negotiable invariants — verdicts

1. **Presentation only** — HOLDS. No backend/query/param/action change anywhere
   in the plan; disabled controls provably inert (native `disabled`, no state).
2. **Shared default compatibility** — **VIOLATED** (F5 rendering; F2 test
   contract). Type-level grid preservation itself is sound.
3. **All three run views align** — HOLDS (`collapseScope={view}`, single board,
   catalog gated out of run controls; T6 S1 covers all three views).
4. **Disabled means unavailable** — HOLDS (native-disabled Button/SearchField;
   out of tab order by nativeness; localized; no handlers that can fire).
5. **Counts are honest** — HOLDS (loaded-cards reduction; label says "loaded";
   recomputed per model; correct after `fetchNextPage` and view swap).
6. **Collapse state is exact** — HOLDS (algorithm traced across all attacked
   cases, §11; render-time reconcile removes the stale-frame window; identity
   preserved on unchanged polls).
7. **No card movement** — HOLDS (no `draggable`, no move API, toggles touch
   only override state).
8. **Explicit foreground selection** — PARTIAL. No background path can select
   or open (only three user-driven call sites write the store), but the
   Attention route's focus-return determinism is wrong (F4).
9. **Inspector authority preserved** — HOLDS (`run-inspector.tsx` untouched;
   drawer composes its existing public props exactly; keying changes only mount
   identity).
10. **Drawer behavior bounded** — HOLDS (closeable in loading/error/content;
    stale `run` wins over `loading`; board is a sibling that never unmounts).
11. **Keyboard ownership exact** — PARTIAL (Escape model verified single-fire
    incl. Radix layering; F4's wrong-origin return breaches the "activation
    origin" clause).
12. **Performance bounded** — **VIOLATED** (F3: lane bodies unbounded ⇒
    virtualization defeated and lane-strip containment moot for height; grid
    path remains bounded).
13. **Design-system compliance** — HOLDS (every color is an existing
    `--ui-*` token — all 13 verified present in `styles.css`; canonical
    `SearchField`/`Button`/`Loader`/`ErrorState`; every new `transition-*`
    carries `motion-reduce:transition-none`; no `animate-*`).
14. **Localization & accessibility complete** — PARTIAL (all nine strings typed
    + translated in all five catalogs with a working non-fallback guard
    (deep-merge verified); F1's duplicate landmark name; F8's RTL anchor).
15. **Failures remain local** — HOLDS (drawer-confined error; board and cards
    remain interactive — asserted in T6 S2; timeline-failure and 409 suites
    retained).
16. **The plan is executable** — **VIOLATED** (F1, F2 block gates; F6, F7
    degrade RED fidelity and the file map).

## 10. Eighteen specific plan premises — verdicts

1. SDK look/feel reproducible via shared `ActivityBoard`, no plugin import —
   **supported** (§6; all geometry/interaction re-derived; no `src/plugins/kanban`
   import anywhere in the plan).
2. Discriminated opt-in prop mode preserves current Kanban page + grid —
   **unsupported as stated** (types/behavior yes; "renders exactly as before"
   no — F5, F2).
3. Optional `HTMLButtonElement` second arg safe for every caller and test
   double — **unsupported** (runtime callers safe by variance;
   `activity-board.test.tsx:33` exact-args double breaks — F2).
4. Pure reconciliation + effect implements the phase/reset rules under polling,
   rapid input, Strict Mode — **supported** (§11; render-time reconcile +
   identity bail-out; Strict-Mode double-invoke idempotent; per-event renders
   make the non-functional `setLaneState(toggle(reconciled,…))` safe).
5. `useGrabScroll` is the correct primitive — **supported** (same hook the SDK
   board uses; interactive-target and scrollbar-gutter guards verified in
   source; per-element panning leaves nested vertical scrolling alone).
6. `w-64`/2rem/flex chain/virtualizer measurement produce the responsive
   full-height layout — **unsupported** (F3: the written chain never delivers a
   definite height to lane bodies; page-root `h-full` viability additionally
   rests on the route container, §16).
7. Loaded count from `model.columns[].cards.length` truthful — **supported**
   (§8 last row).
8. Native-disabled `SearchField` with required `onChange` — **supported**
   (no-op handler + native disabled; canonical disabled styling at the
   primitive; verified against the real component structure).
9. Header composition preserves tabs/focus/RTL/narrow — **insufficiently
   established** for RTL (F8); tab semantics/focus/narrow behavior mirror the
   existing markup and are supported.
10. Selected-run query projects into the drawer without key/interval/
    cancellation changes — **supported** (verbatim reuse; close path drives the
    existing cancellation effect).
11. `selected.error/isLoading/data` sufficient and correctly typed —
    **supported** (drawer's `null | unknown` error accepts TanStack's typed
    error; loading/error/content branches cover the reachable state space;
    stale-data-wins rule sound).
12. Window Escape listener + `defaultPrevented` closes exactly one surface —
    **supported** (verified in installed Radix: `DismissableLayer` handles
    Escape on document **capture** and calls `event.preventDefault()` before
    dismissing — `react-dismissable-layer/dist/index.mjs:91-100` — so an open
    Select/Dialog always wins; drawer and dialogs are mutually exclusive by
    view). Caveat noted in §12 for non-Radix inputs.
13. Board mounted behind drawer preserves scroll/virtualization/pagination/
    selection — **supported** (sibling overlay; no remount; store-driven
    selection).
14. Focus-return ref correct across all listed situations — **unsupported**
    (F4: Attention-driven switch; card unmount → heading fallback and rapid
    card switching are handled).
15. Keying `RunInspector` by `run.run_id` resets tabs/drafts, preserves
    evidence/action behavior — **supported** (all inspector state is
    `useState` local; evidence query cache is keyed independently of component
    identity; e2e suites retained).
16. Locale additions + regression catch every new production string —
    **supported** (`defineLocale` deep-merges English, so the
    `not.toBe(english…)` guard genuinely detects omissions; every new
    production string routes through `t.operations.*`; the inspector's own
    hard-coded English `aria-label` is pre-existing, out of scope).
17. Listed tests + `test:workflow-ui` + typecheck + lint + diff cover both
    consumers — **supported** (`test:workflow-ui` verified to include
    `src/app/workflows/`, `src/app/kanban/`, `src/components/activity-board/`;
    `typecheck` spans renderer/electron/e2e projects; `lint` has no
    `--max-warnings`, so the `document`-in-tests warning rule cannot fail it)
    — noting the *coverage quality* gaps of F3/F7.
18. Completable without touching `RunInspector`, SDK Kanban, backend, plugins,
    deps, config, env — **supported** (every corrected finding above also
    respects this; F1's fix is locale + drawer text, not `RunInspector`).

## 11. Lane-state edge-case assessment

Attacked against the exact Task 2 implementation plus Task 3 wiring:

- **Initial render:** `useState(() => reconcile(null, scope, columns))` seeds
  auto state; no flash.
- **All-empty board:** `boardHasCards` false ⇒ `laneIsCollapsed` = override ∥
  false ⇒ all expanded; matches the SDK rule verbatim.
- **empty→occupied→empty / occupied→empty→occupied:** phase maps differ ⇒
  override filtered out on the first flip; re-toggling re-creates it; the
  second flip drops it again. Correct.
- **Removed/added/reordered lanes:** overrides pruned via `liveIds`; added
  lanes get fresh phases; order-insensitive (`recordsEqual` is key-based).
  (Workflows lanes are a fixed five, so this is defensive only.)
- **Unchanged polls with new identities:** `reconcile` returns the *same state
  object* (`samePhases && sameOverrides ? current : …`) ⇒ the effect's
  `setLaneState` bails out; no render loop; premise "polling with unchanged
  phases is a no-op" holds. The plan's own reference-identity test pins this.
- **Stale-frame render:** the component renders from
  `reconcileLaneCollapseState(laneState, scope, model.columns)` computed *in
  render*, not from raw state — a scope or phase change can never paint one
  stale frame; the effect merely converges stored state afterward.
- **Rapid toggles before effects flush:** each click is its own event/render,
  so the closure's `reconciled` is fresh; the only theoretical hazard
  (two toggles inside one batch) is unreachable from the UI. A functional
  update (`setLaneState(cur => toggle(reconcile(cur, scope, columns), …))`)
  would remove even the theoretical case — worth adopting, not a finding.
- **Remounts:** state is component-local ⇒ reset, as designed.
- **Strict Mode:** double effect invocation is idempotent (second call receives
  the converged state and bails); double initializer is pure.
- **Simultaneous pagination/refetch:** new columns arrays reconcile as above;
  pagination only grows `cards`, flipping at most `empty→occupied`, which
  correctly discards stale expansions of previously-empty lanes.

No resurrection or stale-render path found. The algorithm is the strongest part
of the plan.

## 12. Drawer, keyboard, focus, accessibility, responsive, performance assessment

**Semantics.** Nonmodal complementary region (no dialog role, no focus trap, no
backdrop) is the right model for a board-preserving side pane and matches the
design. Defect: the landmark **name** collides with the inner inspector's (F1).
Cards expose selection via `aria-expanded`; the drawer has an `id` but no card
carries `aria-controls="workflow-run-drawer"` — allowed, but wiring it would
cost one attribute (observation, not a finding). Collapse/expand controls carry
correct `aria-expanded`; rails keep label + nonzero count visible per design.

**Escape.** Verified layering: Radix `Select`/`Dialog` handle Escape at
document-capture and `preventDefault` before dismissing, so the drawer's
window-bubble listener correctly stands down; when nothing consumes it, the
drawer closes exactly once and marks the event consumed. One behavior to accept
consciously: plain inputs (the provide-input/feedback field) do not consume
Escape, so Escape mid-draft closes the drawer and the keyed inspector discards
the draft. The SDK drawer behaves identically and the design text ("closes on
Escape exactly once") sanctions it — flagged in §16 as a product-awareness
item, not a finding.

**Focus.** Card-origin capture at activation, `isConnected` check, heading
fallback with `tabIndex={-1}`, view-change path deliberately not refocusing —
all correct and tested. Gap: Attention route (F4). `requestAnimationFrame`
deferral is sound in both jsdom and production (the store update renders before
the frame callback).

**Rapid switching / unmount races.** Switching runs changes the selected query
key → `data` undefined → loader replaces the keyed inspector → fresh mount on
resolve (Overview reset verified against Radix Tabs `data-state`). Close during
loading and failure-then-close both render a closeable shell. Query completion
after close is inert (`enabled` goes false; drawer unmounted). Source-card
unmount (virtualized away / view change) falls back to the heading.

**Responsive.** Strip-owned `overflow-x-auto` + root `overflow-hidden` contain
horizontal overflow in the markup; but the *vertical* chain is broken (F3), and
narrow-width behavior (`w-full sm:w-[min(32rem,calc(100%-2rem))]`, visible
close control) is sound. Header wraps (`flex-wrap`, `min-w-0`); RTL anchor
defect (F8). Tests assert class fragments, not computed layout — inherent to
jsdom; the acceptance checklist should carry the computed-behavior checks the
tests cannot (see F3 correction).

**Performance.** Grid virtualization contract intact; lane-mode virtualization
defeated by F3 and unguarded by F7. No new polling, no drawer-owned queries, no
animation work; unchanged-poll reference identity preserved at the lane-state
layer and untouched at the query layer.

## 13. Strict-TDD command/file/type/ordering audit

- **Paths/symbols:** every cited existing path, line range, prop, test helper,
  and i18n key was checked — all accurate (details in §17). Notably: all six
  locale insertion ranges are correct to the line; `renderView`'s undocumented
  selection-restore behavior is exactly what the plan's Task 6 tests require;
  `WorkflowRunView` includes `'workflows'`; `Button` supports
  `sm`/`xs`/`icon-xs`/`ghost`/`secondary`/`text`; `Loader` supports
  `label` + `type="lemniscate-bloom"`; `Tabs` sets `data-state`;
  Radix tab triggers activate on `mouseDown` (matching existing test idiom).
- **Commands:** `npx vitest run <files>` from `apps/desktop` — valid;
  `npx tsc -p . --noEmit` — valid (renderer project);
  `npm run test:workflow-ui` / `typecheck` / `lint` — all exist as claimed;
  `npx prettier --check …` — prettier 3.9.5 is a direct devDependency;
  `git diff --check` / `--cached --name-only` — fine.
- **RED honesty:** T1 ✔; T2 ✔; T3 new tests ✔ (traced: grid renders the region
  but no rail/empty-label/`aria-expanded`, so each fails for the stated
  reason); T4 locale half never RED and two T6 tests pass pre-change (F6);
  T5 ✔; T6's drawer-content and inspector-reset tests RED correctly against
  the current below-board composition.
- **GREEN completeness:** T1/T2/T4/T5 snippets are implementable verbatim and
  internally type-consistent (TS aliased-discriminant narrowing in T3 S5 is
  valid TypeScript). T3 GREEN contradicts an existing test (F2); T3/T6 omit the
  height chain (F3); T6 GREEN cannot satisfy its own Step 2 test (F1).
- **Neighboring regressions at boundaries:** existing focus, stale, locale,
  409, cleanup, e2e, typed-artifact suites are all scheduled at the right
  steps; the one missed neighbor is `activity-board.test.tsx:33` (F2). The
  performance file is listed but never edited (F7).
- **Staging scopes:** every `git add` names only plan-owned files or the
  activity-board directory (which contains only plan files); pre-existing
  user-owned untracked files are outside every staged path; Task 6's
  `git diff --cached --name-only` check is a good final guard.
- **No placeholders/conditionals:** none found — every step is concrete; the
  only "may" language is descriptive, not implementation choice.
- **Silent out-of-scope requirements:** none — no `RunInspector`, SDK-plugin,
  backend, Electron-main, dependency, config, or env change is needed by any
  step, including after applying the §15 corrections.

Commands actually executed during this review are limited to read-only
inspection (§18); no test suite was run because every premise needing runtime
proof was resolvable from source (and the plan's own baseline claims 63 green
tests, which was not re-verified and is not load-bearing for any finding).

## 14. Verified complete (and why)

- **Input integrity:** both artifacts hash-matched; repo state matched the
  prompt exactly; nothing was mutated (only this report was written).
- **Approach A boundary:** no plugin import, presentation-only shared board,
  feature-owned drawer/header — verified against the real module graph.
- **Query/action preservation:** every query key, interval, enablement,
  cancellation effect, mutation body, 409 branch, and cleanup flow in
  `index.tsx` is reproduced verbatim or left untouched by the plan (§8).
- **Lane-state algorithm:** exhaustively attacked, correct (§11), and honestly
  a cleaner restatement of the SDK's phase-in-state pattern (whose own history
  — `1238efce1 fix(desktop): track kanban lane phase in state, not a mirrored
  ref` — the plan implicitly learned from; it also does not trip the repo's
  atom-mirror ESLint bans, which target `ref.current` writes, not `setState`).
- **Escape model:** verified against installed Radix source, not assumption.
- **i18n machinery:** `defineLocale` deep-merge semantics make the plan's
  fallback-regression design sound; all nine keys, five catalogs, insertion
  points, and the extended regression test check out; Arabic's partial catalog
  is handled correctly by the plan's additions.
- **Primitives:** `SearchField`, `Button`, `ErrorState`, `Loader`, `Tabs`,
  `useGrabScroll` each inspected; every prop/variant/class the plan consumes
  exists.
- **Tokens:** all 13 CSS custom properties referenced by `HEALTH_TONE` and the
  drawer/card/header chrome exist in `styles.css`.
- **Test-infrastructure claims:** `renderView`, `run()`, mock seams,
  `mouseDown` tab idiom, `within`, `TRANSLATIONS` import — all present and
  compatible with the plan's new tests.
- **Production paths inspected:** every file in the prompt's four inspection
  lists, plus `src/i18n/{define-locale,context,catalog}.ts`,
  `src/app/contrib/surfaces.tsx`, `src/types/hermes.ts`, `src/app/kanban/*`,
  `src/plugins/kanban/api.ts`, `src/styles.css`, and both e2e suites.

## 15. Required corrections (severity, then dependency order)

1. **F3** — specify the full height chain (non-scrolling run-view column;
   `shrink-0` Attention/cleanup; `min-h-0 flex-1` board wrapper and lane
   strip), reconcile the design's "main scrollable content region" sentence,
   and add the lane-mode virtualization gate. (Do first — it may adjust Task 3
   and Task 6 JSX that other fixes touch.)
2. **F1** — de-collide the drawer landmark name in all five locales; scope the
   Task 6 Step 2 queries; add the distinct-landmark assertion.
3. **F2** — amend the existing grid `onOpenCard` assertion inside Task 3 with
   its own RED→GREEN note.
4. **F4** — wire the Attention route into the focus-origin ref and add the
   Attention-focus regression test.
5. **F5** — decide: gate the card restyle on `appearance === 'lane'` (default)
   **or** record the product decision that the Kanban grid adopts it and amend
   invariant 2's wording; add the corresponding guard test either way.
6. **F6** — reorder Task 4 into true RED→GREEN; relabel the two Task 6 guard
   tests.
7. **F7** — fix the Task 3 file list; add the lane-appearance performance case
   (falls out of correction 1).
8. **F8** — switch the header snippet to logical `ms-auto`/`me-2`; add an
   ar-locale check to the acceptance checklist.

## 16. Unresolved product decisions, unverified premises, evidence to resolve

- **Kanban grid card adoption (F5):** product must choose gating vs adoption;
  evidence to close: an updated design sentence or the gated implementation.
- **Design-internal layout tension (feeds F3):** "cleanup … inside the main
  scrollable content region" vs "expanded lane is … full available board
  height" cannot both hold as written; the design owner must pick the
  non-scrolling-column reading (recommended, matches the SDK reference) and
  say so.
- **Page-root `h-full` viability:** the route wrapper is `div.contents`
  (`surfaces.tsx:192-205`), so `h-full` resolves against the zone body. The
  SDK plugin board already uses `h-full` successfully as a full-page surface,
  which suggests the container provides definite height, but the `page()`
  route container was not proven identical to the plugin surface container.
  Evidence to resolve: launch the app once on the branch and confirm the
  Workflows page fills the zone with no page-level vertical scrollbar in
  catalog view. (This is exactly the kind of runtime check the repo's own
  merge guidance prescribes for layout claims.)
- **Escape-discards-draft:** Escape while typing in the provide-input field
  closes the drawer and loses the draft (keyed remount). Design-conformant and
  SDK-parity, but the design never says it *chose* this; a one-line
  acknowledgment in the design would prevent it resurfacing as a bug report.
- **D17 test gap:** "background query updates never select a card or open the
  drawer" has no dedicated test; the guarantee is structural (no polling path
  writes the store). Acceptable, but a cheap regression test (advance a
  refetch, assert `$workflowSelectedRunId` unchanged and no `complementary`)
  would convert inspection into proof.
- **Not re-verified:** the design's "63 tests pass" baseline claim (not
  load-bearing); actual vitest runtime behavior of the plan's new tests (all
  conclusions derive from source semantics of Testing Library/vitest/React,
  each of which was checked at the API-contract level).

## 17. Source-grounding coverage for cited paths/symbols

| Citation (plan/design) | Verdict |
| --- | --- |
| `apps/desktop/src/components/ui/search-field.tsx:10-91` (props, `effectivePlaceholder`, clear/Tip/loading structure) | VERIFIED |
| `apps/desktop/src/components/ui/search-field.test.tsx` (to create; absent today) | VERIFIED (absent as claimed) |
| `src/components/ui/__tests__/no-native-title.test.ts` | VERIFIED |
| `apps/desktop/DESIGN.md:154-164` SearchField bullet | VERIFIED (bullet at ~158–161; replacement text matches existing prose) |
| `activity-board/types.ts:1-31`, `activity-board.tsx:1-25`, `virtual-card-column.tsx:1-97` | VERIFIED |
| `activity-board.test.tsx:1-94` / `activity-board.performance.test.tsx:1-58` | VERIFIED (files are 88/54 lines; ranges slightly over-broad — AMBIGUOUS only in the trivial sense) |
| `useGrabScroll` (`@/hooks/use-grab-scroll`) signature `{ grabbing, onMouseDown }` | VERIFIED |
| `cn` (`@/lib/utils`) | VERIFIED (used throughout) |
| `t.operations.loadMore` / `dataStale` / `workflows` / `workflowViews` / `activeBoard` / `history` / `archive` / tab labels (`overview`…`recovery`, en 'Timeline events', 'Verified artifacts') / `common.close` / `ui.search.clear` | VERIFIED (en.ts lines 2005–2203, 15) |
| i18n insertion ranges: `types.ts:1676-1815`, `en.ts:2004-2160`, `ar.ts:1660-1680`, `ja.ts:1836-1985`, `zh.ts:2196-2338`, `zh-hant.ts:1777-1920` | VERIFIED (each `operations` block opens at the cited first line) |
| `index.test.tsx:880-940` locale regression (`stringKeys`, `english`, `copy`) | VERIFIED (test at line 890) |
| `WorkflowRunView` (incl. `'workflows'`), `WorkflowRunSnapshot` (fixture fields/required keys), `WorkflowTimelineEvent`, `WorkflowRunStatus 'running'` | VERIFIED (`src/types/hermes.ts:420, 704, interface body`) |
| `$workflowSelectedRunId` / `selectWorkflowRun` (persisted atom) | VERIFIED (`store.ts:3-8`) |
| `RunInspector` props (`actionsDisabled?`, `events?`, `onAction?`, `run`) and root aside label | VERIFIED (`run-inspector.tsx:23-28, 196`) |
| `mutateRun`, `mutation`, `actionPending`, `selected`, `events`, `busyExpression` (aria-busy), `PAGE_INSET_X`, `model`, `runs.fetchNextPage`, cleanup JSX | VERIFIED (`index.tsx:141-360`) |
| `renderView(client, initialTab)` helper semantics | VERIFIED (`index.test.tsx:119-142`) |
| `Button` `size="sm"/"xs"/"icon-xs"`, `variant="ghost"/"secondary"/"destructive"/"text"`, native disabled styling, prop spread (`role`, `aria-selected`) | VERIFIED (`button.tsx`) |
| `Codicon name="filter"` | VERIFIED (same icon/size used at `plugins/kanban/board.tsx:900`) |
| `Loader` `label` + `type="lemniscate-bloom"` → `aria-label` | VERIFIED (`loader.tsx` LOADER_TYPES; `loader-native.tsx:100`) |
| `ErrorState` `{ title, className }` | VERIFIED (`error-state.tsx:31-46`) |
| Radix Tabs `data-state`, mousedown activation idiom | VERIFIED (`tabs.tsx`; existing `index.test.tsx:1183` etc.) |
| Radix Escape `preventDefault` before dismiss | VERIFIED (`@radix-ui/react-dismissable-layer/dist/index.mjs:91-100`) |
| CSS tokens `--ui-{yellow,red,green,orange,purple,text-tertiary,text-quaternary,bg-elevated,bg-quaternary,row-hover-background,row-active-background,stroke-tertiary,accent}` | VERIFIED (`styles.css`) |
| `test:workflow-ui`, `typecheck`, `lint`, prettier dep | VERIFIED (`package.json:48-57,159`) |
| SDK reference behaviors (w-64/w-8/vertical-rl/auto rule/phase-drop/grab-scroll/drawer shell/Escape/filter+search header) | VERIFIED (`plugins/kanban/board.tsx`, `drawer.tsx`, `api.ts`, `kanban.css`) |
| `workflowBoardModel` scopeLabel (`t.operations.workflows` → 'Workflows' → aria-label 'Workflows activity board') | VERIFIED |
| Kanban consumer contract (`app/kanban/index.tsx`, `adapter.ts`, `task-inspector.tsx`) | VERIFIED |
| `document.documentElement.dir` RTL switch | VERIFIED (`i18n/context.tsx:66`) |
| Route mount (`page()` wrapper, `div.contents`) | VERIFIED (`contrib/surfaces.tsx:192-205`) — downstream zone height UNCHECKABLE statically (§16) |
| Design claim "63 tests pass" baseline | UNCHECKABLE in this review (not re-run; not load-bearing) |

No MISSING citations were found — every existing path/symbol the plan names
exists; the two AMBIGUOUS/UNCHECKABLE rows are noted above.

## 18. Command / evidence ledger

All commands ran read-only from
`/Users/coreyellis/code/github.com/cmetech/otto_hermes/hermes-agent` (repo
root) or `apps/desktop` as noted. Durations were interactive (<2 s) unless
noted. Kind: G = git/state, H = hash, S = source read, X = existence/grep,
N = node_modules inspection.

| # | Command (abridged) | Dir | Result | Kind |
| --- | --- | --- | --- | --- |
| 1 | `git status --short --branch; git branch --show-current; git rev-parse HEAD; git rev-parse origin/base; git worktree list --porcelain` | root | branch `base`, HEAD `5c9d8a4e6…`, origin/base `786f8dc01…`, 44 worktrees, expected untracked only | G |
| 2 | `shasum -a 256 <design> <plan>` | root | both hashes match the prompt | H |
| 3 | `Read` both immutable inputs (full) | root | design 529 lines; plan 1597 lines | S |
| 4 | `wc -l` over all 40 prompt-listed inspection files | apps/desktop | line counts recorded (drove read strategy) | X |
| 5 | `Read` activity-board `{types,activity-board,virtual-card-column,test,performance.test}` (full) | apps/desktop | grid contract, card classes, virtualizer, line-33 assertion | S |
| 6 | `Read` workflows `{index.tsx,store.ts,detail-query.ts,adapter.ts,attention-inbox.tsx,run-inspector.tsx}` (full) | apps/desktop | queries/mutations/composition/inspector aside label/attention wiring | S |
| 7 | `Read` `index.test.tsx:1-220` (helpers/mocks) | apps/desktop | `renderView` restore semantics, `run()` fixture, mock seams | S |
| 8 | `Read` `search-field.tsx`, `use-grab-scroll.ts`, `button.tsx`, `error-state.tsx`, `eslint.config.mjs` (full) | apps/desktop | primitive contracts; lint rules (atom-mirror bans; test `document` = warn) | S |
| 9 | `grep package.json` scripts; `cat tabs.tsx`; `sed loader.tsx:1-80` | apps/desktop | `test:workflow-ui`/`typecheck`/`lint`/prettier; Radix tabs; LOADER_TYPES incl. `lemniscate-bloom` | X |
| 10 | `grep aria-label loader-native.tsx`; `cat app/kanban/{index,adapter,task-inspector}` | apps/desktop | Loader label forwarding; Kanban consumer contract | S |
| 11 | `grep`/`sed` `types/hermes.ts` (`WorkflowRunView`, `WorkflowRunSnapshot`, `WorkflowRunStatus`, `WorkflowTimelineEvent`) | apps/desktop | type facts for premises 3/11 and fixtures | S |
| 12 | `cat workflow-operations.e2e.test.tsx` (full) | apps/desktop | e2e flows compatible with drawer composition | S |
| 13 | `ls app/workflows/`; targeted greps of `index.test.tsx` (`Laptop diagnostic`, `fireEvent.click`, `mouseDown`, region/aria-busy patterns) + `sed 700-760,940-1000,1405-1536` | apps/desktop | dialog test files exist; no placement-brittle assertions; mouseDown idiom; attention/aria-busy/cleanup suites read | X/S |
| 14 | `Read runtime.test.ts` (full); greps for `activeBoard`/ops keys/`close:`/operations line starts across 6 i18n files | apps/desktop | runtime fallback semantics; all key/line-range claims verified; **ar lacks `activeBoard`** → follow-up #20 | S/X |
| 15 | `grep -rn "catches signal and dependency copy" src/`; `sed index.test.tsx:880-945` | apps/desktop | locale regression test verified at line 890 with `stringKeys`/`english`/`copy` | S |
| 16 | token loop `grep -c -- "--$tok:" styles.css` (13 tokens) | apps/desktop | all present | X |
| 17 | greps + `sed` over `plugins/kanban/board.tsx` (55-70, 405-470, 1250-1330, 1380-1420), `drawer.tsx` (337, 638, 735), `cat kanban.css` | apps/desktop | SDK geometry, auto/override algorithm, strip, drawer shell/Escape, animations | S |
| 18 | `ls ui/__tests__`; `sed DESIGN.md:140-175` | apps/desktop | `no-native-title.test.ts` exists; SearchField bullet located | X/S |
| 19 | `Read apps/desktop/AGENTS.md` (full); `grep` root `AGENTS.md` process sections | root | guidance conformance (focus, background events, testing habits) | S |
| 20 | `sed ar.ts:1655-1695`; `head ar.ts`; `cat define-locale.ts`; `grep TRANSLATIONS catalog.ts` | apps/desktop | ar is a partial locale; `defineLocale` deep-merges English ⇒ regression guard sound | S |
| 21 | `git log --oneline` for activity-board, workflows/index.tsx, plugins/kanban/board.tsx, store.ts | apps/desktop | intent history (`b4fd68b33` reusable board; `1238efce1` phase-in-state fix; `c9285a585` attention/history) | G |
| 22 | `grep name="filter"` src; codicons css located | apps/desktop | filter icon precedent at `board.tsx:900` | X |
| 23 | `grep` kanban app tests for class/arg assertions; `cat app/kanban/index.test.tsx` | apps/desktop | smoke-only; no class assertions ⇒ F5 ships silently | X |
| 24 | `sed`/`grep` `@radix-ui/react-dismissable-layer/dist/index.mjs:80-110`, `react-use-escape-keydown/dist/index.mjs` | root n_m | Escape capture + `preventDefault()` before dismiss | N |
| 25 | `grep dir=`/`rtl` in i18n + `ms-auto` usage | apps/desktop | `context.tsx:66` RTL switch; logical utilities precedented | X |
| 26 | `grep WorkflowsView` src; `sed contrib/surfaces.tsx` page wrapper | apps/desktop | route mount via `div.contents` (§16 open item) | S |
| 27 | `grep collapsedLanes plugins/kanban` | apps/desktop | `api.ts:49,100` — SDK persistence vs plan in-memory adaptation | X |

No test suites, builds, or state-changing commands were executed. No command
reported here was skipped or imagined; several exploratory greps that returned
empty (shell aliasing/quoting artifacts, re-run with `command`) are superseded
by the successful re-runs listed.

---

**Final verdict: `BLOCK`** — resolve F1–F5 (and ideally F6–F8) with the §15
corrections, then re-review Tasks 3, 5, and 6 only; Tasks 1, 2, and 4 are
sound apart from the Task 4 step ordering.

IMPLEMENTATION MUST NOT BEGIN UNTIL CRITICAL AND IMPORTANT PLAN FINDINGS ARE RESOLVED.
