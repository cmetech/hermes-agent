# Workflow Empty Lanes Collapsed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every empty Desktop workflow lane default to a collapsed, individually expandable rail in Active Board, History, and Archive.

**Architecture:** Keep the behavior in the shared pure lane-collapse policy used by the collapsible `ActivityBoard`. Automatic state depends only on the lane's occupancy; existing scope and phase reconciliation continues to own ephemeral user overrides.

**Tech Stack:** React 19, TypeScript, Vitest, Testing Library, nanostores, TanStack Query.

## Global Constraints

- Empty lane: collapsed by default.
- Occupied lane: expanded by default.
- Preserve accessible manual expand/collapse controls and focus restoration.
- Preserve overrides until lane occupancy or `collapseScope` changes.
- Do not change the standalone Kanban plugin or workflow data behavior.
- Follow strict RED → GREEN → refactor and commit the tested change atomically.

---

### Task 1: Collapse all empty workflow lanes

**Files:**
- Modify: `apps/desktop/src/components/activity-board/lane-collapse.test.ts:23-57`
- Modify: `apps/desktop/src/components/activity-board/activity-board.test.tsx:124-143`
- Modify: `apps/desktop/src/app/workflows/index.test.tsx:201-233`
- Modify: `apps/desktop/src/components/activity-board/lane-collapse.ts:46-70`
- Modify: `apps/desktop/src/components/activity-board/activity-board.tsx:43-79`

**Interfaces:**
- Consumes: `ActivityBoardColumn.cards`, `LaneCollapseState.overrides`, and `collapseScope`.
- Produces: `laneIsCollapsed(state: LaneCollapseState, column: ActivityBoardColumn): boolean`.
- Produces: `toggleLaneCollapse(state: LaneCollapseState, column: ActivityBoardColumn): LaneCollapseState`.

- [ ] **Step 1: Write the failing policy, component, and workflow-view tests**

In `lane-collapse.test.ts`, replace the whole-board-empty expectation with:

```ts
it('auto-collapses every empty lane when the whole board is empty', () => {
  const columns = [column('queued', 0), column('active', 0)]
  const state = reconcileLaneCollapseState(null, 'board', columns)

  expect(columns.map(item => laneIsCollapsed(state, item, false))).toEqual([true, true])
})
```

In `activity-board.test.tsx`, replace the whole-board-empty test with:

```tsx
it('collapses every lane when the entire board is empty and lets users expand one', () => {
  const empty = {
    ...collapsibleModel,
    columns: collapsibleModel.columns.map(column => ({ ...column, cards: [], count: 0 }))
  }

  render(
    <ActivityBoard
      collapseScope="history"
      laneCopy={laneCopy}
      layout="collapsible-lanes"
      model={empty}
      onLoadMore={vi.fn()}
      onOpenCard={vi.fn()}
    />
  )

  expect(screen.getAllByRole('button', { name: /^Expand / })).toHaveLength(2)
  expect(screen.queryByText('No runs')).toBeNull()

  fireEvent.click(screen.getByRole('button', { name: 'Expand Queued' }))
  expect(within(screen.getByRole('region', { name: 'Queued, 0' })).getByText('No runs')).toBeTruthy()
  expect(screen.getByRole('button', { name: 'Collapse Queued' }).getAttribute('aria-expanded')).toBe('true')
})
```

In `app/workflows/index.test.tsx`, add this integration regression after the existing three-view layout test:

```tsx
it.each([
  ['board', 'Active board'],
  ['history', 'History'],
  ['archive', 'Archive']
] as const)('defaults every lane in the empty %s view to collapsed', async (view, label) => {
  $workflowSelectedRunId.set(null)
  listWorkflowRuns.mockResolvedValue({ next_cursor: null, runs: [], schema_version: 1 })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  await renderView(client, 'workflows')
  fireEvent.click(screen.getByRole('tab', { name: label }))

  expect(await screen.findByLabelText('0 loaded workflow runs')).toBeTruthy()
  const board = await screen.findByLabelText('Workflows activity board')

  expect(within(board).getAllByRole('button', { name: /^Expand / })).toHaveLength(5)
  expect(within(board).queryByText('No runs')).toBeNull()
  expect(listWorkflowRuns).toHaveBeenCalledWith(undefined, view)
})
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
npm --workspace apps/desktop run test:ui -- \
  src/components/activity-board/lane-collapse.test.ts \
  src/components/activity-board/activity-board.test.tsx \
  src/app/workflows/index.test.tsx
```

Expected: FAIL because the pure policy returns `false` for empty lanes when `boardHasCards` is false; the component and three workflow views consequently expose expanded empty lanes instead of Expand buttons.

- [ ] **Step 3: Implement the occupancy-only automatic policy**

In `lane-collapse.ts`, replace the two exported functions with:

```ts
export function laneIsCollapsed(state: LaneCollapseState, column: ActivityBoardColumn): boolean {
  return state.overrides[column.id] ?? column.cards.length === 0
}

export function toggleLaneCollapse(
  state: LaneCollapseState,
  column: ActivityBoardColumn
): LaneCollapseState {
  const automatic = column.cards.length === 0
  const next = !laneIsCollapsed(state, column)
  const overrides = { ...state.overrides }

  if (next === automatic) {
    delete overrides[column.id]
  } else {
    overrides[column.id] = next
  }

  return { ...state, overrides }
}
```

Update every call in `lane-collapse.test.ts` to remove the obsolete boolean argument. The occupied-lane assertions remain unchanged:

```ts
expect(laneIsCollapsed(state, columns[0]!)).toBe(true)
expect(laneIsCollapsed(state, columns[1]!)).toBe(false)
```

In `activity-board.tsx`, delete `boardHasCards` and call the simplified interfaces:

```tsx
const collapsed = laneIsCollapsed(reconciled, column)
```

```tsx
onToggleCollapsed={() => setLaneState(toggleLaneCollapse(reconciled, column))}
```

- [ ] **Step 4: Run the focused tests and verify GREEN**

Run:

```bash
npm --workspace apps/desktop run test:ui -- \
  src/components/activity-board/lane-collapse.test.ts \
  src/components/activity-board/activity-board.test.tsx \
  src/app/workflows/index.test.tsx
```

Expected: all three files pass, including three empty workflow-view cases and manual expansion of one empty lane.

- [ ] **Step 5: Run the workflow UI regression suite**

Run:

```bash
npm --workspace apps/desktop run test:workflow-ui
```

Expected: all workflow, Kanban, routing, activity-board, and workflow-topology UI tests pass.

- [ ] **Step 6: Run static verification**

Run:

```bash
npm --workspace apps/desktop run typecheck
npm --workspace apps/desktop run lint
git diff --check
```

Expected: typecheck exits 0, lint reports zero errors, and `git diff --check` exits 0.

- [ ] **Step 7: Inspect and commit the atomic change**

Run:

```bash
git diff -- \
  apps/desktop/src/components/activity-board/lane-collapse.ts \
  apps/desktop/src/components/activity-board/lane-collapse.test.ts \
  apps/desktop/src/components/activity-board/activity-board.tsx \
  apps/desktop/src/components/activity-board/activity-board.test.tsx \
  apps/desktop/src/app/workflows/index.test.tsx
git status --short
git add \
  apps/desktop/src/components/activity-board/lane-collapse.ts \
  apps/desktop/src/components/activity-board/lane-collapse.test.ts \
  apps/desktop/src/components/activity-board/activity-board.tsx \
  apps/desktop/src/components/activity-board/activity-board.test.tsx \
  apps/desktop/src/app/workflows/index.test.tsx
git commit -m "fix(desktop): collapse empty workflow lanes"
```

Expected: one implementation commit containing only the shared policy, its component wiring, and the three levels of regression coverage.
