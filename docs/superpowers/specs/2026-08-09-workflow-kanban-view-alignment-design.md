# Workflow Kanban View Alignment Design

**Date:** 2026-08-09

**Status:** Approved for implementation planning

**Revision:** 2026-08-10 — adversarial findings F1–F8 resolved

**Scope:** Hermes Desktop Workflows page presentation for Active board, History,
Archive, and the selected-run inspector

## Summary

Hermes Desktop will align the Workflows run views with the newer SDK Kanban
plugin's compact lane treatment. Active board, History, and Archive will render
as a horizontally scrollable set of expandable card lanes. Empty lanes collapse
automatically into narrow vertical rails; occupied lanes remain expanded. The
page header will adopt the Kanban toolbar hierarchy with a run count and visible
filter/search affordances, while filter, search, and profile-selection behavior
remain explicitly unavailable in this phase.

Selecting a workflow run will no longer append its inspector below the board.
It will open a nonmodal drawer from the right side of the Workflows page. The
drawer preserves the existing Overview, Timeline, Attempts, Logs, Outputs,
Verified artifacts, and Recovery tabs and every authoritative workflow action.

The workflow backend, run-list views, query keys, lifecycle authority, polling,
pagination, evidence projection, and compare-and-set mutations do not change.
This is a Desktop presentation and interaction change.

## Grounded baseline

The current implementation already provides the required data and action seams:

- `WorkflowsView` fetches `board`, `history`, and `archive` through the same
  bounded `listWorkflowRuns(cursor, view)` query seam.
- `workflowBoardModel()` maps every run list into the same five visual lanes:
  Queued, Active, Needs attention, Completed, and Failed / stopped.
- `ActivityBoard` owns the current fixed responsive grid and per-column card
  virtualization.
- `RunInspector` owns seven existing detail tabs, evidence queries, input and
  reconciliation controls, and authoritative `next_actions` buttons.
- `$workflowSelectedRunId` is the selected-run identity. Closing the future
  drawer can clear this identity and naturally stop detail/event queries.
- The SDK Kanban plugin demonstrates the target presentation: fixed-width
  expanded lanes, empty-lane vertical rails, horizontal grab scrolling, a
  compact count/filter/search header, and a nonmodal absolute right drawer.

The focused baseline is green as of this design: 63 tests pass across the
shared activity-board unit/performance tests and the Workflows view tests.

## Goals

- Give Active board, History, and Archive one consistent Kanban-style lane
  presentation.
- Collapse empty workflow lanes into vertical rails without hiding their label
  or count.
- Let users explicitly expand or collapse any workflow lane.
- Keep large run collections virtualized and bounded.
- Show a compact Workflows header with navigation, count, filter, and search
  affordances.
- Make unavailable filter/search behavior honest and accessible.
- Open selected-run detail at the right side instead of below the board.
- Preserve every current inspector tab, evidence query, action, conflict
  recovery behavior, and typed-artifact path.
- Preserve page context: background updates may update cards but never select a
  run or open the drawer.
- Keep the change inside Desktop presentation and feature-owned state.

## Non-goals

- No backend, REST, gateway, workflow plugin, RunStore, or persistence-schema
  change.
- No profile picker, cross-profile enumeration, or profile filter.
- No functional text search, status filter, health filter, origin filter, or
  query-parameter extension.
- No arbitrary workflow card drag/drop or generic status mutation.
- No Kanban task operations such as assign, move, bulk select, create, delete,
  reclaim, or profile grouping.
- No change to the Workflows catalog table, View dialog, or Review & Run dialog.
- No redesign of Attention inbox or evidence cleanup semantics.
- No extraction of the entire SDK Kanban board into core and no core dependency
  on `src/plugins/kanban`.
- No new dependency, model tool, configuration key, or `HERMES_*` environment
  variable.

## Approaches considered

### A. Extend the shared ActivityBoard with a collapsible-lane layout — selected

Add a narrow presentation mode to the Desktop-owned `ActivityBoard`. The shared
component continues to own layout, lane accessibility, card virtualization,
selection styling, and responsive overflow. `WorkflowsView` continues to own
run selection, queries, actions, and its drawer.

Advantages:

- preserves the existing visual-only shared boundary;
- retains the proven virtualized card column;
- avoids importing task-specific plugin behavior;
- keeps workflow status authority and actions unchanged;
- creates a reusable presentation for the built-in Kanban fallback without
  forcing it to adopt the mode in this phase.

Cost: the shared component gains a small layout contract and collapse state.

### B. Copy the SDK Kanban `Column` into Workflows

This is initially fast, but that component is coupled to drag/drop, locked
targets, task creation, assignee lanes, selection, bulk actions, and plugin
localization. Removing those branches would create a second lane component that
looks the same only until one copy changes.

**Rejected:** duplicated presentation and the wrong domain dependency.

### C. Refactor the complete SDK Kanban board and Workflows onto one new SDK API

This would maximize immediate code sharing, but it expands the public plugin SDK
and must accommodate Kanban drag/drop and workflow read-only actions at once.
That is materially larger than the requested Workflows alignment.

**Deferred:** two concrete consumers justify shared visual primitives, but this
phase does not need a public generic board framework.

## Architecture

The design preserves the selected visual-only boundary:

```text
workflow REST projections
        |
        v
WorkflowsView queries + workflowBoardModel
        |
        +--> ActivityBoard (collapsible lane presentation + virtualization)
        |
        +--> WorkflowRunDrawer
                  |
                  +--> RunInspector (tabs, evidence, actions)
```

`ActivityBoard` does not gain a generic move operation. `WorkflowRunDrawer`
does not fetch or mutate workflow data independently. It receives the selected
query state and closes through `selectWorkflowRun(null)`.

## Component design

### ActivityBoard

`ActivityBoard` gains optional presentation inputs while preserving its current
model and callbacks:

```ts
interface ActivityBoardProps {
  collapseScope?: string
  layout?: 'grid' | 'collapsible-lanes'
  model: ActivityBoardModel
  onLoadMore: (columnId: string, cursor: string) => void
  onOpenCard: (card: ActivityBoardCard, origin?: HTMLButtonElement) => void
  selectedCardId?: null | string
}
```

- `layout` defaults to `grid` so existing consumers do not change implicitly.
- Workflows passes `layout="collapsible-lanes"` for `board`, `history`, and
  `archive`.
- `collapseScope` is the current run-list view. Changing it starts a new
  in-memory lane-collapse session, so Archive choices do not leak into Active
  board or History.
- `selectedCardId` gives the open run's card a stable selected treatment.

The collapsible layout is a height-bounded `flex` lane strip with contained
horizontal overflow. The Workflows run-view column gives `ActivityBoard` the
remaining height through an unbroken `min-h-0 flex-1` chain; each expanded lane
then scrolls its own virtualized card body. The strip uses the existing shared
grab-scroll hook. The page itself must never gain horizontal overflow or become
the long-lane scroll container.

An expanded lane is 16rem wide, full available board height, and contains:

- a state-tone dot;
- uppercase lane label;
- count;
- a keyboard-operable collapse control;
- its existing virtualized card list and bounded Load more control.

A collapsed lane remains a named region containing a 2rem-wide native button.
The button contains:

- the same state-tone dot;
- the label in vertical writing mode;
- the count when nonzero;
- an accessible name of `Expand <lane>`;
- an `aria-expanded="false"` state.

Expanded lane headers expose `aria-expanded="true"`. Collapse and expansion
change presentation only. They never alter workflow state, filtering, queries,
pagination, or card membership.

### Lane collapse behavior

Lane state is interaction state owned by the mounted `ActivityBoard`, not a
backend or persistent preference.

- When at least one lane contains cards, empty lanes start collapsed and
  occupied lanes start expanded.
- When every lane is empty, every lane starts expanded so the board teaches its
  structure instead of displaying only rails.
- A user may expand an empty lane or collapse an occupied lane.
- An override lasts while the lane remains in its current empty/occupied phase
  and while the same `collapseScope` remains mounted.
- When a lane changes between empty and occupied, its stale override is removed
  and the automatic rule applies again.
- Changing among Active board, History, and Archive resets to the automatic
  state for the new scope.
- Reloading or remounting the page resets lane state. Persistence is outside
  this phase.

This matches the useful behavior of the SDK Kanban board without copying its
plugin storage contract.

### Workflow cards

The card remains a native button and preserves its existing accessible name.
In `collapsible-lanes` appearance only, its visual treatment aligns with SDK
Kanban cards:

- elevated surface token;
- tertiary hairline border;
- 2px left state/health accent;
- compact title, exact state, and metadata hierarchy;
- quiet hover treatment;
- explicit selected border/background treatment;
- no drag cursor or draggable attribute.

Health-to-tone mapping is presentation-only and centralized beside the shared
card renderer. It must use design tokens or existing semantic tokens; no new raw
color literals are introduced.

The default `grid` appearance retains its existing card classes, badge styling,
and visual hierarchy byte-for-byte. The built-in Kanban page does not adopt the
new card chrome in this phase. Both appearances supply the activated native
button as the optional callback origin, but only the Workflows lane appearance
uses selected and health-accent styling.

Virtualization remains active above 50 cards per lane. Reordering from a poll or
delta must preserve focus on the same keyed card.

### Workflows header

The current page heading and four navigation tabs become one compact,
wrap-capable header. It contains:

1. `Workflows` as the page heading;
2. Workflows, Active board, History, and Archive navigation;
3. on the three run views only, a loaded-run count badge;
4. a disabled filter button;
5. a disabled canonical `SearchField`.

The catalog view retains the same page heading and navigation but does not show
the loaded-run count, filter, or search controls.

The controls intentionally reserve the final information hierarchy without
pretending to work:

- the loaded-run count is derived from the cards currently present in the
  bounded run-list pages; it does not claim to be an unpaginated server total;
- the filter button uses the native disabled state and an accessible label
  equivalent to `Run filters coming soon`;
- the SearchField uses a native disabled input and a localized placeholder
  equivalent to `Search runs — coming soon`;
- neither control stores input, changes a query key, filters cards, or emits a
  request;
- disabled styling is owned by the shared primitive and remains legible in all
  themes.

The shared `SearchField` therefore gains an optional `disabled?: boolean` prop
that forwards to the native input and applies its canonical disabled styling.
The Desktop design-system documentation is updated with this supported state.

No profile control appears in this phase. The existing active gateway profile
continues to scope workflow requests.

### WorkflowRunDrawer

A new workflow-owned component wraps the selected query state and
`RunInspector` in a nonmodal side drawer.

```ts
interface WorkflowRunDrawerProps {
  actionsDisabled: boolean
  error: null | unknown
  events?: WorkflowTimelineEvent[]
  loading: boolean
  onAction: (action: string, body?: Record<string, unknown>) => void
  onClose: () => void
  run: null | WorkflowRunSnapshot
  selectedRunId: string
}
```

The drawer:

- is absolutely positioned against a `relative`, full-height Workflows page;
- occupies the right edge without a modal backdrop;
- is 32rem wide when space permits and full-width on narrow workspaces;
- uses an elevated surface and a single left hairline;
- has a close button with an accessible name and no tooltip;
- closes on Escape exactly once;
- renders the canonical Loader while selected detail is loading;
- renders the canonical ErrorState when selected detail cannot load;
- renders `RunInspector` unchanged when a run is available;
- never opens from polling, attention updates, run completion, or any other
  background event.

The outer drawer's localized complementary-region name is equivalent to
`<workflow> run details`. It must be distinct from the unchanged inner
`RunInspector` landmark, whose existing accessible name is `<workflow> run
inspector`.

The board remains mounted behind the drawer. Opening the drawer does not reset
lane scroll, pagination, or card virtualization. The selected card stays
highlighted. `ActivityBoard` supplies the activated native button as the
optional second `onOpenCard` argument, and `WorkflowsView` retains it only as a
focus-return ref. `AttentionInbox` likewise supplies its activated native button
when opening a run. Closing returns focus to the latest foreground activation
origin when it is still connected; otherwise focus returns to the Workflows
heading. Switching the selected run through any foreground surface must replace,
never retain, an older origin.

The drawer's body scrolls independently. Its width must accommodate the existing
overview definition list, horizontally scrollable tab list, action controls,
logs, and typed artifacts without page-level overflow.

### RunInspector

`RunInspector` remains the source of truth for:

- Overview;
- Timeline events;
- Attempts;
- Logs;
- Outputs;
- Verified artifacts;
- Recovery;
- evidence query activation by selected tab;
- input and loop-signal confirmation controls;
- reconciliation outcome controls;
- authoritative lifecycle action buttons.

Moving it into a drawer must not change its evidence query keys, stale times,
action labels, or action-enablement rules. Selecting another card reuses the
drawer shell but resets inspector tab state to Overview by keying the inspector
to `run.run_id`.

## Page composition

`WorkflowsView` becomes a relative, full-height flex column with contained
overflow.

- The compact header is the first shrink-free row.
- The catalog continues in an ordinary `min-h-0 flex-1 overflow-y-auto` content
  area.
- Each run view is a non-scrolling `min-h-0 flex-1 flex-col` region.
- Attention inbox is a non-scrolling, shrink-free row above the board and
  remains semantically unchanged.
- The board receives the remaining height through a `min-h-0 flex-1` wrapper;
  its lane strip and expanded lane bodies own their respective horizontal and
  vertical scrolling.
- Evidence cleanup is a non-scrolling, shrink-free row below History and
  Archive and retains its explicit preview-token flow. Archive never implies
  deletion.
- `WorkflowRunDrawer` is a sibling overlay anchored to the page root, not a
  child appended after `ActivityBoard`.

Changing the navigation view clears the selected run before the next view
renders, which closes the drawer and cancels selected-run/event polling through
the existing query enablement and cancellation effects.

## Data flow and authority

The data flow remains:

1. Active profile selects the authenticated workflow API scope.
2. The selected run-list view chooses `listWorkflowRuns(cursor, view)`.
3. `workflowBoardModel()` maps server snapshots into visual lanes and cards.
4. `ActivityBoard` renders and locally collapses lanes.
5. A direct card or Attention activation records its native-button origin and
   sets `$workflowSelectedRunId`.
6. Existing detail and event queries load the selected run.
7. `WorkflowRunDrawer` renders loading, error, or `RunInspector` content.
8. Inspector actions call the existing compare-and-set mutation seam.
9. Closing or switching views clears `$workflowSelectedRunId` and stops
   selected-run work.

Server truth remains authoritative. Lane state, drawer visibility, and disabled
future controls are presentation state only.

## Loading, empty, stale, and error states

- Initial run-list loading keeps the canonical page loader.
- A run-list error without cached data keeps the current workflow-unavailable
  state.
- Stale cached board data keeps the shared stale status without closing the
  drawer or clearing selection.
- An all-empty board renders the expanded lane structure. Each empty expanded
  lane contains a localized `No runs` message rather than collapsing into a
  wall of rails.
- A selected-run loading state opens the drawer immediately with a Loader so
  the card activation has visible feedback.
- A selected-run error stays confined to the drawer; the board remains usable
  and the drawer can be closed.
- Timeline/evidence failures remain confined to their current inspector tabs
  and do not disable authoritative actions unless existing logic requires it.

## Responsive behavior

- The lane strip owns horizontal scrolling at every width; the page and app
  shell do not scroll horizontally.
- Expanded lanes retain a stable 16rem width instead of shrinking until cards
  become unreadable.
- Empty rails retain a stable 2rem width.
- Header content wraps without overlapping the drawer or escaping page gutters.
- Header spacing uses logical inline utilities so the toolbar anchors to the
  correct end in both LTR and RTL locales.
- On narrow workspaces the drawer covers the page width and keeps a visible
  close control. It remains nonmodal and Escape-dismissable.
- Opening or closing the drawer must not cause lane geometry animation or mount
  every virtualized card.
- No functional transition ignores `prefers-reduced-motion`.

## Accessibility and keyboard behavior

- Lanes remain named regions with label and count.
- Collapsed rails and expanded collapse controls are native buttons with
  `aria-expanded` and localized accessible names.
- Cards remain native buttons and preserve Enter/Space activation.
- Selected card state is programmatically exposed with `aria-expanded`; the
  selected card is the control whose run inspector is open.
- The drawer is a named complementary region, not a modal dialog.
- Escape closes only the drawer and does not also trigger a control beneath it.
- Escape from an ordinary inspector text field also closes the drawer and
  discards that unsubmitted local draft; nested Radix surfaces that consume
  Escape close themselves first.
- Closing restores focus to the latest foreground activation origin (board card
  or Attention button) or the page heading fallback.
- Disabled future controls are native-disabled, skipped by ordinary tab order,
  and named as unavailable rather than appearing broken.
- Drawer tabs retain the existing Radix tabs keyboard behavior and horizontal
  overflow.

## Localization

All new user-facing copy is added together to English, Arabic, Japanese,
Simplified Chinese, and Traditional Chinese Desktop locales. This includes:

- expand/collapse lane labels when existing common copy cannot express the
  interpolated lane name;
- empty workflow lane copy;
- run filters coming-soon label;
- disabled run-search placeholder;
- selected-run drawer accessible name;
- selected-run drawer loading/error labels if existing common copy is
  insufficient.

No literal user-facing string is added directly to Workflows JSX.

## Testing strategy

### Shared ActivityBoard tests

- default `grid` mode preserves the current consumer contract;
- default `grid` cards retain their existing visual classes while both modes
  supply the activated button origin;
- collapsible mode expands occupied lanes and collapses empty lanes;
- an all-empty board exposes every lane expanded;
- rail and header controls toggle one lane without changing card membership;
- empty/occupied phase transitions discard stale manual overrides;
- changing `collapseScope` resets automatic lane state;
- selected card styling and programmatic selected state follow
  `selectedCardId`;
- card activation and bounded Load more callbacks remain exact;
- focus survives unrelated card reordering;
- 1,000-card columns remain virtualized in both grid and collapsible-lane
  appearances;
- 320, 768, and 1440px layouts contain horizontal overflow inside the board;
- reduced-motion behavior remains compliant.

### SearchField tests

- `disabled` forwards to the native input;
- disabled styling is canonical and the input cannot emit `onChange`;
- existing enabled, clear, loading, hint, and trailing-action behavior remains
  unchanged.

### Workflow drawer tests

- card click opens a complementary region immediately in loading state;
- successful detail renders all seven existing tabs in the side drawer;
- outer run-details and inner run-inspector complementary landmarks have
  distinct accessible names;
- drawer content replaces the former below-board inspector placement;
- close button and Escape clear selected identity once;
- close restores focus to the originating card;
- switching from a board card to an Attention item replaces the return origin,
  and close restores focus to the Attention button;
- switching among Active board, History, and Archive closes the drawer;
- selecting another run resets the tab to Overview;
- detail failure is confined to the drawer and leaves board cards usable;
- closing cancels/avoids selected event polling through the current query seam;
- background query updates never select a card or open the drawer;
- inspector mutations, 409 recovery, evidence queries, and typed-artifact
  behavior retain their current tests.

### Workflows page tests

- catalog view shows heading/navigation without run toolbar controls;
- each run view shows loaded-run count plus disabled filter and search
  affordances;
- filter/search activation cannot change queries or displayed card membership;
- board, history, and archive still call the exact server view seam;
- Attention inbox remains above the board;
- History and Archive retain the explicit evidence-cleanup preview/execute
  contract;
- all new copy is complete across every Desktop locale.
- Arabic renders the run toolbar with logical end alignment.

## Acceptance criteria

1. Active board, History, and Archive use fixed-width expandable lanes with
   empty lanes represented as vertical rails.
2. Users can expand or collapse lanes with mouse and keyboard.
3. The Workflows run-view header visibly contains a count, filter control, and
   search field; filter and search are honestly disabled and perform no work.
4. No profile or filter logic, backend request field, or query-key variation is
   added.
5. Activating a run card opens a right-side nonmodal drawer instead of rendering
   details below the board.
6. The drawer exposes all seven existing inspector tabs and all currently valid
   workflow actions.
7. Closing the drawer or switching views clears selected-run polling and
   restores sensible focus.
8. Large columns remain virtualized, and page-level horizontal overflow is
   absent at 320, 768, and 1440px; at 1440px a 300-card lane scrolls inside the
   lane rather than making the page the vertical scroll container.
9. Attention and explicit archive/cleanup semantics are unchanged.
10. Existing focused tests plus new lane, drawer, responsive, accessibility,
    localization, type-check, and lint checks pass.
11. The default ActivityBoard grid keeps its existing card chrome; only the
    Workflows collapsible-lane appearance adopts compact Kanban styling.
12. The drawer and nested inspector expose distinct localized complementary
    landmark names, and Attention activation becomes the latest focus-return
    origin.

## Expected implementation surface

The implementation plan covers focused changes in:

- `apps/desktop/src/components/activity-board/activity-board.tsx`
- `apps/desktop/src/components/activity-board/virtual-card-column.tsx`
- `apps/desktop/src/components/activity-board/types.ts`
- `apps/desktop/src/components/activity-board/*.test.tsx`
- `apps/desktop/src/components/ui/search-field.tsx`
- `apps/desktop/src/components/ui/search-field.test.tsx`
- `apps/desktop/src/app/workflows/index.tsx`
- `apps/desktop/src/app/workflows/attention-inbox.tsx`
- a new focused workflow drawer component and test
- `apps/desktop/src/app/workflows/index.test.tsx`
- Desktop locale files for `en`, `ar`, `ja`, `zh`, and `zh-hant`
- `apps/desktop/DESIGN.md` for the SearchField disabled-state contract

No Python, workflow plugin, gateway, Electron main-process, SDK Kanban plugin,
or dashboard bundle file should be required.

## Adversarial implementation-review remediation

The Fable 5 implementation review identified three shell-integration defects.
They are resolved within the existing presentation-only boundary; workflow
queries, mutations, selection authority, inspector behavior, and plugin code do
not change.

### Escape ownership and focus restoration

The run drawer participates in the existing application escape-layer registry
at a new page-drawer priority below narrow overlays. It registers only while the
Workflows pane is visible, yields Escape to every higher layer, and unregisters
when hidden or unmounted. This preserves the normal in-page behavior while
preventing a hidden drawer from consuming Escape behind another pane or overlay.

Closing still clears selected-run identity. Focus returns to the activation
origin or Workflows heading only when keyboard focus is currently within the
Workflows page; closing from another focused surface does not steal focus.

### Drawer separator ownership

The drawer remains a nonmodal right-side `aside`, but its left hairline is
painted by an inner full-height wrapper. The pane shell intentionally strips
edge chrome from zone-level `aside` elements, so the inner wrapper owns the
in-page drawer separator without weakening the global shell rule.

### Bounded Attention layout

The Attention region remains above the board and remains shrink-free, but it is
bounded to a minority of the available run-view height. Its item list owns
vertical overflow after that cap, keeping the Attention heading visible while
guaranteeing that the board and History/Archive cleanup controls retain a
reachable layout. No item semantics, ordering, actions, or backend data change.

### Regression proof

Tests are added before production changes and must fail for the reviewed defect:

- a higher-priority app layer prevents the drawer from handling Escape;
- a mounted-but-hidden Workflows pane does not register or handle drawer Escape;
- closing does not restore focus when focus is outside the Workflows page;
- the drawer separator belongs to an inner element rather than the shell-reset
  `aside`;
- the Attention region exposes a bounded, internally scrolling list contract.

Targeted tests run after each correction. Final verification reruns the complete
workflow UI suite, all Desktop TypeScript projects, lint, and `git diff --check`.
