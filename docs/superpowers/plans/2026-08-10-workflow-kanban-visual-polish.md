# Workflow Kanban Visual Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every Desktop Workflows lane a stable Kanban-style color and widen the workflow run inspector to a responsive `40rem` desktop cap.

**Architecture:** The workflow adapter remains authoritative for workflow lane semantics and supplies an optional presentation tone through `ActivityBoardColumn`. The shared activity board renders that tone for expanded and collapsed lane dots while retaining its current health-based fallback for consumers that do not supply one; card borders remain independently health-colored. The existing nonmodal drawer changes only its responsive width class.

**Tech Stack:** React 19, TypeScript, Tailwind CSS v4 utility classes, Radix tabs, Vitest, Testing Library.

## Global Constraints

- Use only the approved workflow lane palette: Queued `var(--ui-blue)`, Active `var(--ui-green)`, Needs attention `var(--ui-yellow)`, Completed `var(--ui-text-tertiary)`, Failed / stopped `var(--ui-red)`.
- The workflow adapter owns the lane-to-tone mapping; the generic activity board must not infer workflow meaning from lane IDs.
- Explicit lane tones must remain visible when lanes are empty or collapsed.
- Card left borders remain derived from each card's health.
- The drawer width contract is exactly `w-full sm:w-[min(40rem,calc(100%-2rem))]`.
- Keep horizontal tab overflow as the fallback for long translations; do not wrap or vertically stack tabs.
- Do not change workflow state, persistence, APIs, localization, focus, Escape ownership, landmarks, selection, actions, or scroll ownership.
- Do not add raw color literals or dependencies.
- Preserve unrelated worktree changes and untracked documentation.

---

### Task 1: Add source-owned workflow lane tones

**Files:**
- Modify: `apps/desktop/src/components/activity-board/types.ts:17-23`
- Modify: `apps/desktop/src/components/activity-board/virtual-card-column.tsx:25-33,55-59,89-127,177-193`
- Modify: `apps/desktop/src/components/activity-board/activity-board.test.tsx:42-45,61-90`
- Modify: `apps/desktop/src/app/workflows/adapter.ts:4-10,85-130`
- Modify: `apps/desktop/src/app/workflows/adapter.test.ts:18-31`

**Interfaces:**
- Consumes: Existing `ActivityBoardColumn`, `ActivityBoardCard['health']`, `HEALTH_TONE`, and workflow `COLUMNS` projection.
- Produces: `ActivityBoardColumn.tone?: string`, with workflow columns supplying the five approved CSS-variable values.
- Preserves: Consumers that omit `tone` continue to derive the lane dot from the first card's health, then fall back to `var(--ui-text-quaternary)` for an empty lane.

- [ ] **Step 1: Write the failing workflow-adapter tone test**

Add this assertion to the existing `keeps lifecycle authority in exact states while grouping for presentation` test in `apps/desktop/src/app/workflows/adapter.test.ts`:

```ts
expect(model.columns.map(column => [column.id, column.tone])).toEqual([
  ['queued', 'var(--ui-blue)'],
  ['active', 'var(--ui-green)'],
  ['attention', 'var(--ui-yellow)'],
  ['completed', 'var(--ui-text-tertiary)'],
  ['stopped', 'var(--ui-red)']
])
```

- [ ] **Step 2: Run the adapter test and verify RED**

Run from `apps/desktop`:

```bash
npx vitest run --project ui src/app/workflows/adapter.test.ts
```

Expected: FAIL because every projected `column.tone` is `undefined`.

- [ ] **Step 3: Write the failing expanded/collapsed lane rendering test**

Add this test to `apps/desktop/src/components/activity-board/activity-board.test.tsx`:

```tsx
it('keeps explicit lane tones across collapsed and expanded lanes without replacing card health tones', () => {
  const tonedModel = {
    ...collapsibleModel,
    columns: [
      { ...collapsibleModel.columns[0]!, tone: 'var(--ui-blue)' },
      { ...collapsibleModel.columns[1]!, tone: 'var(--ui-purple)' }
    ]
  } satisfies ActivityBoardModel

  render(
    <ActivityBoard
      collapseScope="board"
      laneCopy={laneCopy}
      layout="collapsible-lanes"
      model={tonedModel}
      onLoadMore={vi.fn()}
      onOpenCard={vi.fn()}
    />
  )

  const collapsedQueued = screen.getByRole('region', { name: 'Queued, 0' })
  expect(collapsedQueued.querySelector<HTMLElement>('span[style]')?.style.backgroundColor).toBe('var(--ui-blue)')

  fireEvent.click(within(collapsedQueued).getByRole('button', { name: 'Expand Queued' }))

  const expandedQueued = screen.getByRole('region', { name: 'Queued, 0' })
  expect(expandedQueued.querySelector<HTMLElement>('header span[style]')?.style.backgroundColor).toBe('var(--ui-blue)')

  const active = screen.getByRole('region', { name: 'Active, 1' })
  expect(active.querySelector<HTMLElement>('header span[style]')?.style.backgroundColor).toBe('var(--ui-purple)')
  expect(screen.getByRole('button', { name: 'Run one, running' }).style.borderLeftColor).toBe('var(--ui-green)')
})
```

The intentionally purple test lane proves that a source-owned lane tone and the healthy card's green border remain separate signals.

- [ ] **Step 4: Run the activity-board test and verify RED**

Run from `apps/desktop`:

```bash
npx vitest run --project ui src/components/activity-board/activity-board.test.tsx
```

Expected: FAIL because the collapsed empty lane resolves to `var(--ui-text-quaternary)` and the occupied lane resolves from card health instead of their supplied tones.

- [ ] **Step 5: Add the optional shared lane-tone interface**

Update `ActivityBoardColumn` in `apps/desktop/src/components/activity-board/types.ts`:

```ts
export interface ActivityBoardColumn {
  cards: readonly ActivityBoardCard[]
  count: number
  id: string
  label: string
  nextCursor: null | string
  tone?: string
}
```

Keep `tone` optional so the legacy grid Kanban adapter and any future consumer do not need a presentation value.

- [ ] **Step 6: Project the approved tones from the workflow adapter**

Replace the workflow `COLUMNS` tuple in `apps/desktop/src/app/workflows/adapter.ts` with:

```ts
const COLUMNS = [
  ['queued', 'Queued', 'var(--ui-blue)'],
  ['active', 'Active', 'var(--ui-green)'],
  ['attention', 'Needs attention', 'var(--ui-yellow)'],
  ['completed', 'Completed', 'var(--ui-text-tertiary)'],
  ['stopped', 'Failed / stopped', 'var(--ui-red)']
] as const
```

Update the column projection to destructure and return the tone:

```ts
const columns: ActivityBoardColumn[] = COLUMNS.map(([id, label, tone]) => {
  const selected = runs.filter(run => columnId(run) === id)

  return {
    cards: selected.map(run => ({
      ariaDescription: [
        run.workflow,
        exactState(run, options.scheduledLabel),
        run.health,
        run.provenance?.source,
        run.provenance?.assurance
      ]
        .filter(Boolean)
        .join(', '),
      badges: [
        ...(run.provenance
          ? [
              {
                icon: ORIGIN_ICONS[run.provenance.source],
                label: run.provenance.source,
                tone: 'muted' as const
              }
            ]
          : []),
        ...(ATTENTION_HEALTH.has(run.health)
          ? [
              {
                label: run.health.replaceAll('_', ' '),
                tone: 'danger' as const
              }
            ]
          : []),
        ...(run.current_nodes?.[0] ? [{ label: run.current_nodes[0], tone: 'notice' as const }] : []),
        { label: `${run.progress.completed_nodes}/${run.progress.total_nodes}` }
      ],
      exactState: exactState(run, options.scheduledLabel),
      health: health(run),
      id: run.run_id,
      title: run.workflow,
      updatedAt: Date.parse(run.updated_at)
    })),
    count: selected.length,
    id,
    label,
    nextCursor: options.nextCursor ?? null,
    tone
  }
})
```

- [ ] **Step 7: Prefer explicit lane tones in the shared renderer**

Change only the lane-tone resolution in `apps/desktop/src/components/activity-board/virtual-card-column.tsx`:

```ts
const laneTone =
  column.tone ?? (column.cards[0] ? HEALTH_TONE[column.cards[0].health] : 'var(--ui-text-quaternary)')
```

Keep both lane-dot `style={{ backgroundColor: laneTone }}` call sites and the card style below unchanged:

```tsx
style={lane ? { borderLeftColor: HEALTH_TONE[card.health], ...virtualStyle } : virtualStyle}
```

This is the critical separation between category color and card health color.

- [ ] **Step 8: Run focused tests and verify GREEN**

Run from `apps/desktop`:

```bash
npx vitest run --project ui src/app/workflows/adapter.test.ts src/components/activity-board/activity-board.test.tsx src/components/activity-board/lane-collapse.test.ts src/components/activity-board/activity-board.performance.test.tsx
```

Expected: all tests PASS, including the explicit empty/collapsed lane color and card-health-border assertions.

- [ ] **Step 9: Run the TypeScript gate**

Run from `apps/desktop`:

```bash
npm run typecheck
```

Expected: PASS with no type errors in either adapter consumer or test fixtures that omit the optional `tone`.

- [ ] **Step 10: Commit the lane-tone slice**

```bash
git add \
  apps/desktop/src/components/activity-board/types.ts \
  apps/desktop/src/components/activity-board/virtual-card-column.tsx \
  apps/desktop/src/components/activity-board/activity-board.test.tsx \
  apps/desktop/src/app/workflows/adapter.ts \
  apps/desktop/src/app/workflows/adapter.test.ts
git commit -m "feat(desktop): color workflow activity lanes"
```

---

### Task 2: Widen the workflow run inspector and run regression gates

**Files:**
- Modify: `apps/desktop/src/app/workflows/workflow-run-drawer.test.tsx:47-65`
- Modify: `apps/desktop/src/app/workflows/workflow-run-drawer.tsx:69-75`

**Interfaces:**
- Consumes: Existing `WorkflowRunDrawer` nonmodal aside, inner frame, and responsive Tailwind width classes.
- Produces: Exact responsive width contract `w-full sm:w-[min(40rem,calc(100%-2rem))]`.
- Preserves: Drawer anchoring, viewport margin, border ownership, loader/error states, inspector keying, Escape-layer behavior, and tab-list horizontal overflow.

- [ ] **Step 1: Write the failing responsive-width test**

In the existing `renders distinct run-details and run-inspector complementary regions` test, add the following assertions immediately after the current `absolute` and `right-0` checks:

```ts
expect(classTokens(drawer)).toContain('w-full')
expect(classTokens(drawer)).toContain('sm:w-[min(40rem,calc(100%-2rem))]')
expect(classTokens(drawer)).not.toContain('sm:w-[min(32rem,calc(100%-2rem))]')
```

- [ ] **Step 2: Run the drawer test and verify RED**

Run from `apps/desktop`:

```bash
npx vitest run --project ui src/app/workflows/workflow-run-drawer.test.tsx
```

Expected: FAIL because the drawer still carries `sm:w-[min(32rem,calc(100%-2rem))]` and lacks the `40rem` token.

- [ ] **Step 3: Apply the exact `40rem` responsive width**

In `apps/desktop/src/app/workflows/workflow-run-drawer.tsx`, replace only the aside's width class:

```tsx
className="absolute inset-y-0 right-0 z-20 w-full bg-(--ui-bg-elevated) sm:w-[min(40rem,calc(100%-2rem))]"
```

Do not change `run-inspector.tsx`: its existing `TabsList` retains `max-w-full justify-start overflow-x-auto`, which is the approved long-translation fallback.

- [ ] **Step 4: Run the drawer test and verify GREEN**

Run from `apps/desktop`:

```bash
npx vitest run --project ui src/app/workflows/workflow-run-drawer.test.tsx
```

Expected: all drawer tests PASS, including landmarks, loading/error states, close behavior, and Escape ownership.

- [ ] **Step 5: Run the complete workflow UI regression suite**

Run from `apps/desktop`:

```bash
npm run test:workflow-ui
```

Expected: PASS across workflow views, Kanban adapter compatibility, activity-board accessibility, virtualization, drawer behavior, and workflow operation tests.

- [ ] **Step 6: Run static and brand-neutral gates**

Run from `apps/desktop`:

```bash
npm run typecheck
npm run lint
npm run check:brand-neutral
```

Expected: typecheck and brand-neutral checks PASS; lint exits zero with no new warning attributable to these files.

- [ ] **Step 7: Check the final diff scope**

Run from the repository root:

```bash
git diff --check
git status --short
git diff --stat HEAD~1
```

Expected: no whitespace errors; only the two Task 2 files are uncommitted at this point. Existing unrelated untracked documentation remains untouched.

- [ ] **Step 8: Commit the drawer-width slice**

```bash
git add \
  apps/desktop/src/app/workflows/workflow-run-drawer.tsx \
  apps/desktop/src/app/workflows/workflow-run-drawer.test.tsx
git commit -m "style(desktop): widen workflow run inspector"
```

- [ ] **Step 9: Verify the committed implementation state**

Run from the repository root:

```bash
git log -3 --oneline
git diff --check origin/base...HEAD
git status --short --branch
```

Expected: the lane-tone and drawer-width commits are present, the implementation diff has no whitespace errors, and only pre-existing unrelated untracked files remain.
