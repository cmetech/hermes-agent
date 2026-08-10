# Workflow Kanban View Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Align Desktop Workflows Active board, History, and Archive with the SDK Kanban plugin's collapsible-lane presentation and open the existing tabbed run inspector in a nonmodal right-side drawer.

**Architecture:** Preserve `workflowBoardModel` and every backend/query/action seam. Extend the Desktop-owned `ActivityBoard` with an opt-in collapsible-lane mode, add feature-owned header and drawer components, and keep `WorkflowsView` responsible only for query orchestration, selected-run identity, and composition.

**Tech Stack:** React 19, TypeScript 6, TanStack Query, TanStack Virtual, Testing Library, Vitest, Tailwind CSS 4, Desktop i18n, existing Hermes UI primitives.

## Global Constraints

- Work on `base`; literal `main` is synchronization-only.
- Follow `apps/desktop/AGENTS.md` and `apps/desktop/DESIGN.md`.
- No backend, REST, gateway, workflow plugin, RunStore, persistence-schema, Electron main-process, or dashboard bundle changes.
- No profile picker, cross-profile enumeration, functional search, functional filters, or query-parameter changes.
- No workflow drag/drop and no generic card move operation.
- No import from `apps/desktop/src/plugins/kanban`; reproduce only its visual interaction contract through shared Desktop presentation.
- `ActivityBoard` remains presentation-only; source adapters retain lifecycle meaning and authority.
- `layout="grid"` remains the default so current consumers do not change implicitly.
- Filter and search affordances are native-disabled and perform no work.
- Preserve all seven `RunInspector` tabs, evidence queries, typed-artifact handling, `next_actions`, CAS mutations, and 409 recovery.
- Lane collapse state is in-memory and scoped to `board`, `history`, or `archive`; it is not persisted.
- Large columns remain virtualized above 50 cards.
- Page-level horizontal overflow is forbidden; the lane strip owns contained horizontal scrolling.
- Use existing UI tokens and primitives; introduce no raw color literals or new dependency.
- Add every new string to `en`, `ar`, `ja`, `zh`, and `zh-hant` in the same commit.
- Respect reduced motion and preserve keyboard/focus behavior.
- Do not touch or stage unrelated working-tree files.

## Approved design

Read before implementation:

- `docs/superpowers/specs/2026-08-09-workflow-kanban-view-alignment-design.md`
- `apps/desktop/AGENTS.md`
- `apps/desktop/DESIGN.md`

## File responsibility map

| File | Responsibility |
| --- | --- |
| `apps/desktop/src/components/ui/search-field.tsx` | Canonical native-disabled search state |
| `apps/desktop/src/components/ui/search-field.test.tsx` | Disabled-state regression coverage |
| `apps/desktop/src/components/activity-board/lane-collapse.ts` | Pure lane phase/override reconciliation |
| `apps/desktop/src/components/activity-board/lane-collapse.test.ts` | Pure collapse-state behavior |
| `apps/desktop/src/components/activity-board/types.ts` | Shared layout and lane-copy contracts |
| `apps/desktop/src/components/activity-board/activity-board.tsx` | Grid/collapsible layout selection and lane state ownership |
| `apps/desktop/src/components/activity-board/virtual-card-column.tsx` | Grid column, expanded lane, collapsed rail, cards, virtualization |
| `apps/desktop/src/components/activity-board/activity-board.test.tsx` | Interaction, accessibility, selection, responsive containment |
| `apps/desktop/src/components/activity-board/activity-board.performance.test.tsx` | Large-column virtualization regression |
| `apps/desktop/src/app/workflows/workflow-view-header.tsx` | Workflows navigation and disabled future toolbar |
| `apps/desktop/src/app/workflows/workflow-view-header.test.tsx` | Header mode and disabled-control coverage |
| `apps/desktop/src/app/workflows/workflow-run-drawer.tsx` | Nonmodal right-side run-detail shell |
| `apps/desktop/src/app/workflows/workflow-run-drawer.test.tsx` | Drawer loading/error/content/dismissal coverage |
| `apps/desktop/src/app/workflows/index.tsx` | Query orchestration and composition of header, board, and drawer |
| `apps/desktop/src/app/workflows/index.test.tsx` | End-to-end renderer behavior at the feature seam |
| `apps/desktop/src/i18n/{types,en,ar,ja,zh,zh-hant}.ts` | Complete localized workflow board/drawer copy |
| `apps/desktop/DESIGN.md` | Document the canonical disabled SearchField state |

---

### Task 1: Add a canonical disabled SearchField state

**Files:**
- Create: `apps/desktop/src/components/ui/search-field.test.tsx`
- Modify: `apps/desktop/src/components/ui/search-field.tsx:10-91`
- Modify: `apps/desktop/DESIGN.md:154-164`

**Interfaces:**
- Consumes: existing `SearchFieldProps`, `Button`, `Tip`, and `I18nProvider`.
- Produces: `SearchFieldProps.disabled?: boolean`; when true the native input is disabled, the whole control is visibly unavailable, and clear/trailing actions are not rendered.

- [ ] **Step 1: Write the failing disabled-state test**

Create `apps/desktop/src/components/ui/search-field.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { SearchField } from './search-field'

afterEach(cleanup)

describe('SearchField', () => {
  it('uses a native-disabled input and suppresses trailing actions', () => {
    render(
      <I18nProvider configClient={null} initialLocale="en">
        <SearchField
          aria-label="Search runs — coming soon"
          disabled
          onChange={vi.fn()}
          placeholder="Search runs — coming soon"
          trailingAction={<button type="button">Trailing action</button>}
          value=""
        />
      </I18nProvider>
    )

    const input = screen.getByRole('textbox', { name: 'Search runs — coming soon' }) as HTMLInputElement

    expect(input.disabled).toBe(true)
    expect(input.closest('[aria-disabled="true"]')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Trailing action' })).toBeNull()
  })

  it('preserves enabled input behavior', () => {
    const onChange = vi.fn()

    render(
      <I18nProvider configClient={null} initialLocale="en">
        <SearchField onChange={onChange} placeholder="Search" value="" />
      </I18nProvider>
    )

    fireEvent.change(screen.getByRole('textbox', { name: 'Search' }), { target: { value: 'run' } })
    expect(onChange).toHaveBeenCalledWith('run')
  })
})
```

- [ ] **Step 2: Run the focused test and verify the disabled contract fails**

Run:

```bash
cd apps/desktop
npx vitest run src/components/ui/search-field.test.tsx
```

Expected: the disabled test fails because `disabled` is not forwarded and the trailing action still renders.

- [ ] **Step 3: Implement the native-disabled SearchField state**

Extend the props and component destructuring in `search-field.tsx`:

```tsx
interface SearchFieldProps {
  placeholder: string
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  hints?: string[]
  containerClassName?: string
  inputClassName?: string
  loading?: boolean
  onClear?: () => void
  inputRef?: RefObject<HTMLInputElement | null>
  trailingAction?: ReactNode
  'aria-label'?: string
}
```

Use `disabled = false` in the parameter defaults. Apply the state at the primitive boundary:

```tsx
<div
  aria-disabled={disabled || undefined}
  className={cn(
    'inline-flex min-w-0 max-w-full items-center gap-1.5 border-b border-transparent px-0.5 transition-[color,border-color,opacity]',
    !value && !disabled && 'opacity-30 focus-within:opacity-100',
    disabled && 'pointer-events-none opacity-45',
    containerClassName
  )}
>
```

Forward `disabled` to `<input>`, add `disabled:cursor-default` to its canonical class list, and suppress interactive adornments:

```tsx
<input
  aria-label={ariaLabel ?? placeholder}
  disabled={disabled}
  className={cn(
    'h-7 min-w-0 max-w-full bg-transparent text-xs text-foreground [field-sizing:content] placeholder:text-muted-foreground focus:outline-none disabled:cursor-default',
    inputClassName
  )}
  onChange={event => onChange(event.target.value)}
  placeholder={effectivePlaceholder}
  ref={inputRef}
  type="text"
  value={value}
/>
{!disabled && trailingAction}
{!disabled && loading ? (
  <Loader2 className="pointer-events-none size-3.5 shrink-0 animate-spin text-muted-foreground/70" />
) : !disabled && value ? (
  <Tip label={t.ui.search.clear}>
    <Button aria-label={t.ui.search.clear} onClick={clear} size="icon-xs" variant="ghost">
      <Codicon name="close" size="0.875rem" />
    </Button>
  </Tip>
) : null}
```

Retain the existing clear-button canonical classes when applying this fragment.

- [ ] **Step 4: Document the supported state**

Update the `SearchField` bullet in `apps/desktop/DESIGN.md` to state:

```markdown
- **`SearchField`** — borderless, underline-on-focus, auto-width. The only
  search input. Don't build boxed search bars; don't wrap it in a bordered tile.
  Empty lists hide their search field. Future search surfaces may use its native
  `disabled` state; never render an enabled no-op search control.
```

- [ ] **Step 5: Run focused tests and type-check the primitive**

Run:

```bash
cd apps/desktop
npx vitest run src/components/ui/search-field.test.tsx src/components/ui/__tests__/no-native-title.test.ts
npx tsc -p . --noEmit
```

Expected: both test files pass and renderer type-check exits 0.

- [ ] **Step 6: Commit the primitive change**

```bash
git add apps/desktop/src/components/ui/search-field.tsx apps/desktop/src/components/ui/search-field.test.tsx apps/desktop/DESIGN.md
git commit -m "feat(desktop): add disabled search field state"
```

---

### Task 2: Implement pure collapsible-lane state

**Files:**
- Create: `apps/desktop/src/components/activity-board/lane-collapse.ts`
- Create: `apps/desktop/src/components/activity-board/lane-collapse.test.ts`
- Modify: `apps/desktop/src/components/activity-board/types.ts:1-31`

**Interfaces:**
- Consumes: `ActivityBoardColumn` from `types.ts`.
- Produces: `ActivityBoardLaneCopy`, `LaneCollapseState`, `reconcileLaneCollapseState()`, `laneIsCollapsed()`, and `toggleLaneCollapse()`.

- [ ] **Step 1: Add the shared layout and copy contracts**

Append to `types.ts`:

```ts
export interface ActivityBoardLaneCopy {
  collapse: (lane: string) => string
  empty: string
  expand: (lane: string) => string
}
```

- [ ] **Step 2: Write failing pure-state tests**

Create `lane-collapse.test.ts`:

```ts
import { describe, expect, it } from 'vitest'

import type { ActivityBoardColumn } from './types'
import {
  laneIsCollapsed,
  reconcileLaneCollapseState,
  toggleLaneCollapse
} from './lane-collapse'

const column = (id: string, cards: number): ActivityBoardColumn => ({
  cards: Array.from({ length: cards }, (_, index) => ({
    ariaDescription: `${id} ${index}`,
    badges: [],
    exactState: id,
    health: 'healthy',
    id: `${id}-${index}`,
    title: `${id} ${index}`,
    updatedAt: index
  })),
  count: cards,
  id,
  label: id,
  nextCursor: null
})

describe('collapsible lane state', () => {
  it('auto-collapses only empty lanes when the board contains work', () => {
    const columns = [column('queued', 0), column('active', 1)]
    const state = reconcileLaneCollapseState(null, 'board', columns)

    expect(laneIsCollapsed(state, columns[0]!, true)).toBe(true)
    expect(laneIsCollapsed(state, columns[1]!, true)).toBe(false)
  })

  it('keeps every lane expanded when the whole board is empty', () => {
    const columns = [column('queued', 0), column('active', 0)]
    const state = reconcileLaneCollapseState(null, 'board', columns)

    expect(columns.map(item => laneIsCollapsed(state, item, false))).toEqual([false, false])
  })

  it('stores only deviations from automatic state', () => {
    const columns = [column('queued', 0), column('active', 1)]
    const initial = reconcileLaneCollapseState(null, 'board', columns)
    const expandedEmpty = toggleLaneCollapse(initial, columns[0]!, true)
    const collapsedOccupied = toggleLaneCollapse(expandedEmpty, columns[1]!, true)

    expect(expandedEmpty.overrides).toEqual({ queued: false })
    expect(collapsedOccupied.overrides).toEqual({ active: true, queued: false })
    expect(toggleLaneCollapse(collapsedOccupied, columns[1]!, true).overrides).toEqual({ queued: false })
  })

  it('drops stale overrides when occupancy or scope changes', () => {
    const before = [column('queued', 0), column('active', 1)]
    let state = reconcileLaneCollapseState(null, 'board', before)
    state = toggleLaneCollapse(state, before[0]!, true)
    state = reconcileLaneCollapseState(state, 'board', [column('queued', 1), column('active', 1)])

    expect(state.overrides).toEqual({})
    expect(reconcileLaneCollapseState(state, 'history', before).scope).toBe('history')
    expect(reconcileLaneCollapseState(state, 'history', before).overrides).toEqual({})
  })

  it('preserves reference identity when reconciliation changes nothing', () => {
    const columns = [column('active', 1)]
    const state = reconcileLaneCollapseState(null, 'board', columns)

    expect(reconcileLaneCollapseState(state, 'board', columns)).toBe(state)
  })
})
```

- [ ] **Step 3: Run the pure-state tests and verify they fail**

Run:

```bash
cd apps/desktop
npx vitest run src/components/activity-board/lane-collapse.test.ts
```

Expected: FAIL because `lane-collapse.ts` does not exist.

- [ ] **Step 4: Implement exact phase reconciliation and overrides**

Create `lane-collapse.ts` with this public shape:

```ts
import type { ActivityBoardColumn } from './types'

type LanePhase = 'empty' | 'occupied'

export interface LaneCollapseState {
  overrides: Readonly<Record<string, boolean>>
  phases: Readonly<Record<string, LanePhase>>
  scope: string
}

const phaseOf = (column: Pick<ActivityBoardColumn, 'cards'>): LanePhase =>
  column.cards.length === 0 ? 'empty' : 'occupied'

function recordsEqual<T extends boolean | string>(
  left: Readonly<Record<string, T>>,
  right: Readonly<Record<string, T>>
): boolean {
  const keys = Object.keys(left)
  return keys.length === Object.keys(right).length && keys.every(key => left[key] === right[key])
}

export function reconcileLaneCollapseState(
  current: LaneCollapseState | null,
  scope: string,
  columns: readonly ActivityBoardColumn[]
): LaneCollapseState {
  const phases = Object.fromEntries(columns.map(column => [column.id, phaseOf(column)]))

  if (!current || current.scope !== scope) {
    return { overrides: {}, phases, scope }
  }

  const liveIds = new Set(columns.map(column => column.id))
  const overrides = Object.fromEntries(
    Object.entries(current.overrides).filter(([id]) => liveIds.has(id) && current.phases[id] === phases[id])
  )
  const samePhases = recordsEqual(current.phases, phases)
  const sameOverrides = recordsEqual(current.overrides, overrides)

  return samePhases && sameOverrides ? current : { overrides, phases, scope }
}

export function laneIsCollapsed(
  state: LaneCollapseState,
  column: ActivityBoardColumn,
  boardHasCards: boolean
): boolean {
  return state.overrides[column.id] ?? (boardHasCards && column.cards.length === 0)
}

export function toggleLaneCollapse(
  state: LaneCollapseState,
  column: ActivityBoardColumn,
  boardHasCards: boolean
): LaneCollapseState {
  const automatic = boardHasCards && column.cards.length === 0
  const next = !laneIsCollapsed(state, column, boardHasCards)
  const overrides = { ...state.overrides }

  if (next === automatic) {
    delete overrides[column.id]
  } else {
    overrides[column.id] = next
  }

  return { ...state, overrides }
}
```

The exact key/value comparison preserves reference identity without depending on serialized object order or adding a dependency.

- [ ] **Step 5: Run the pure-state tests**

```bash
cd apps/desktop
npx vitest run src/components/activity-board/lane-collapse.test.ts
```

Expected: 5 tests pass.

- [ ] **Step 6: Commit the state contract**

```bash
git add apps/desktop/src/components/activity-board/types.ts apps/desktop/src/components/activity-board/lane-collapse.ts apps/desktop/src/components/activity-board/lane-collapse.test.ts
git commit -m "feat(desktop): model collapsible activity lanes"
```

---

### Task 3: Render collapsible lanes and selected workflow cards

**Files:**
- Modify: `apps/desktop/src/components/activity-board/activity-board.tsx:1-25`
- Modify: `apps/desktop/src/components/activity-board/virtual-card-column.tsx:1-97`
- Modify: `apps/desktop/src/components/activity-board/activity-board.test.tsx:1-94`
- Modify: `apps/desktop/src/components/activity-board/activity-board.performance.test.tsx:1-58`

**Interfaces:**
- Consumes: Task 2 `ActivityBoardLaneCopy`, `LaneCollapseState`, `reconcileLaneCollapseState()`, `laneIsCollapsed()`, and `toggleLaneCollapse()`; existing `useGrabScroll`.
- Produces: discriminated `ActivityBoardProps`, contained collapsible lane rendering, optional selected-card state, and `onOpenCard(card, origin)`.

- [ ] **Step 1: Extend ActivityBoard tests with the collapsible contract**

Add this shared copy and a two-column model to `activity-board.test.tsx`:

```tsx
const laneCopy = {
  collapse: (lane: string) => `Collapse ${lane}`,
  empty: 'No runs',
  expand: (lane: string) => `Expand ${lane}`
}

const collapsibleModel: ActivityBoardModel = {
  ...model,
  columns: [
    { cards: [], count: 0, id: 'queued', label: 'Queued', nextCursor: null },
    model.columns[0]!
  ]
}
```

Add these tests:

```tsx
it('renders empty lanes as accessible rails and toggles them without moving cards', () => {
  render(
    <ActivityBoard
      collapseScope="board"
      laneCopy={laneCopy}
      layout="collapsible-lanes"
      model={collapsibleModel}
      onLoadMore={vi.fn()}
      onOpenCard={vi.fn()}
    />
  )

  const queued = screen.getByRole('region', { name: 'Queued, 0' })
  expect(within(queued).getByRole('button', { name: 'Expand Queued' }).getAttribute('aria-expanded')).toBe('false')
  fireEvent.click(within(queued).getByRole('button', { name: 'Expand Queued' }))
  expect(within(queued).getByText('No runs')).toBeTruthy()
  expect(within(queued).getByRole('button', { name: 'Collapse Queued' }).getAttribute('aria-expanded')).toBe('true')
  expect(screen.getByRole('button', { name: 'Run one, running' })).toBeTruthy()
})

it('expands every lane when the entire board is empty', () => {
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

  expect(screen.queryByRole('button', { name: /^Expand / })).toBeNull()
  expect(screen.getAllByText('No runs')).toHaveLength(2)
})

it('resets lane overrides when the collapse scope changes', () => {
  const view = render(
    <ActivityBoard
      collapseScope="board"
      laneCopy={laneCopy}
      layout="collapsible-lanes"
      model={collapsibleModel}
      onLoadMore={vi.fn()}
      onOpenCard={vi.fn()}
    />
  )

  fireEvent.click(screen.getByRole('button', { name: 'Collapse Active' }))
  expect(screen.getByRole('button', { name: 'Expand Active' })).toBeTruthy()
  view.rerender(
    <ActivityBoard
      collapseScope="archive"
      laneCopy={laneCopy}
      layout="collapsible-lanes"
      model={collapsibleModel}
      onLoadMore={vi.fn()}
      onOpenCard={vi.fn()}
    />
  )
  expect(screen.getByRole('button', { name: 'Collapse Active' })).toBeTruthy()
})
```

- [ ] **Step 2: Add failing selected-card and origin tests**

```tsx
it('exposes the selected card and supplies its native button as the activation origin', () => {
  const open = vi.fn()

  render(
    <ActivityBoard
      collapseScope="board"
      laneCopy={laneCopy}
      layout="collapsible-lanes"
      model={model}
      onLoadMore={vi.fn()}
      onOpenCard={open}
      selectedCardId="one"
    />
  )

  const card = screen.getByRole('button', { name: 'Run one, running' })
  expect(card.getAttribute('aria-expanded')).toBe('true')
  fireEvent.click(card)
  expect(open).toHaveBeenCalledWith(model.columns[0]!.cards[0], card)
})
```

- [ ] **Step 3: Run ActivityBoard tests and verify the new cases fail**

```bash
cd apps/desktop
npx vitest run src/components/activity-board/activity-board.test.tsx
```

Expected: new tests fail because the props and lane rendering do not exist.

- [ ] **Step 4: Add the discriminated ActivityBoard prop contract**

In `activity-board.tsx`, replace the current props interface with:

```tsx
interface ActivityBoardBaseProps {
  model: ActivityBoardModel
  onLoadMore: (columnId: string, cursor: string) => void
  onOpenCard: (card: ActivityBoardCard, origin?: HTMLButtonElement) => void
  selectedCardId?: null | string
}

interface GridActivityBoardProps extends ActivityBoardBaseProps {
  layout?: 'grid'
}

interface CollapsibleActivityBoardProps extends ActivityBoardBaseProps {
  collapseScope: string
  laneCopy: ActivityBoardLaneCopy
  layout: 'collapsible-lanes'
}

type ActivityBoardProps = CollapsibleActivityBoardProps | GridActivityBoardProps
```

Import `useEffect`, `useRef`, and `useState` from React; `cn` from `@/lib/utils`; `useGrabScroll` from `@/hooks/use-grab-scroll`; the Task 2 state functions; and `ActivityBoardLaneCopy`.

- [ ] **Step 5: Implement contained lane state and layout selection**

Inside `ActivityBoard`, seed and reconcile state only for collapsible mode:

```tsx
export function ActivityBoard(props: ActivityBoardProps) {
const { model, onLoadMore, onOpenCard, selectedCardId } = props
const collapsible = props.layout === 'collapsible-lanes'
const scope = collapsible ? props.collapseScope : model.source
const [laneState, setLaneState] = useState(() => reconcileLaneCollapseState(null, scope, model.columns))
const reconciled = reconcileLaneCollapseState(laneState, scope, model.columns)
const boardHasCards = model.columns.some(column => column.cards.length > 0)
const stripRef = useRef<HTMLDivElement>(null)
const { grabbing, onMouseDown } = useGrabScroll(stripRef)

useEffect(() => {
  setLaneState(current => reconcileLaneCollapseState(current, scope, model.columns))
}, [model.columns, scope])
```

Close the component body after the existing empty/model branches and the two layout branches. The reconciliation effect may execute when polling supplies a new column-array identity; `reconcileLaneCollapseState` returns the existing state object when scope and phases are unchanged, so React does not schedule an additional render.

Keep the existing grid JSX byte-for-byte equivalent when `collapsible` is false. For collapsible mode, render:

```tsx
<div
  className={cn('flex min-h-0 min-w-0 gap-2 overflow-x-auto overscroll-contain', grabbing && 'cursor-grabbing')}
  data-layout="collapsible-lanes"
  onMouseDown={onMouseDown}
  ref={stripRef}
>
  {model.columns.map(column => {
    const collapsed = laneIsCollapsed(reconciled, column, boardHasCards)

    return (
      <VirtualCardColumn
        appearance="lane"
        collapsed={collapsed}
        collapseLabel={props.laneCopy.collapse(column.label)}
        column={column}
        emptyLabel={props.laneCopy.empty}
        expandLabel={props.laneCopy.expand(column.label)}
        key={column.id}
        loadMoreLabel={t.operations.loadMore}
        onLoadMore={onLoadMore}
        onOpenCard={onOpenCard}
        onToggleCollapsed={() =>
          setLaneState(toggleLaneCollapse(reconciled, column, boardHasCards))
        }
        selectedCardId={selectedCardId}
      />
    )
  })}
</div>
```

- [ ] **Step 6: Extend VirtualCardColumn for grid, lane, and rail forms**

Extend its props exactly:

```tsx
interface VirtualCardColumnProps {
  appearance?: 'grid' | 'lane'
  collapsed?: boolean
  collapseLabel?: string
  column: ActivityBoardColumn
  emptyLabel?: string
  expandLabel?: string
  loadMoreLabel: string
  onLoadMore: (columnId: string, cursor: string) => void
  onOpenCard: (card: ActivityBoardCard, origin?: HTMLButtonElement) => void
  onToggleCollapsed?: () => void
  selectedCardId?: null | string
}
```

Use these token-only health accents:

```tsx
const HEALTH_TONE: Record<ActivityBoardCard['health'], string> = {
  attention: 'var(--ui-yellow)',
  failed: 'var(--ui-red)',
  healthy: 'var(--ui-green)',
  idle: 'var(--ui-text-tertiary)',
  stale: 'var(--ui-orange)',
  terminal: 'var(--ui-text-quaternary)',
  waiting: 'var(--ui-purple)'
}
```

For a collapsed lane, return a named `<section aria-label={`${column.label}, ${column.count}`}>` containing a full-height 2rem button with vertical label, count, `aria-expanded={false}`, and `onClick={onToggleCollapsed}`.

For an expanded lane, use `flex h-full w-64 shrink-0 flex-col` and a compact header with dot, uppercase label, count, and a collapse button carrying `aria-expanded={true}`. In grid appearance, the scroll body retains `max-h-[65dvh]`; in lane appearance it uses `min-h-0 flex-1`. Both retain `ref={parent}`, vertical overflow containment, and the existing virtualization calculation. Render `<p>{emptyLabel}</p>` only when `column.cards.length === 0`.

Update each card button to:

```tsx
const selected = selectedCardId === card.id

<button
  aria-expanded={selectedCardId === undefined ? undefined : selected}
  aria-label={card.ariaDescription}
  className={cn(
    'block w-full rounded-md border border-(--ui-stroke-tertiary) border-l-2 bg-(--ui-bg-elevated) p-2.5 text-left transition-colors motion-reduce:transition-none hover:bg-(--ui-row-hover-background) focus-visible:outline focus-visible:outline-(--ui-accent)',
    selected && 'border-(--ui-accent) bg-(--ui-row-active-background)'
  )}
  data-activity-card-id={card.id}
  key={card.id}
  onClick={event => onOpenCard(card, event.currentTarget)}
  style={{
    borderLeftColor: HEALTH_TONE[card.health],
    ...(virtualRow
      ? { position: 'absolute', transform: `translateY(${virtualRow.start}px)`, width: '100%' }
      : {})
  }}
  type="button"
>
```

Keep title, exact state, and badges compact. Apply each badge's existing `tone` through token classes (`text-destructive`, `text-(--ui-text-quaternary)`, `text-(--ui-yellow)`, `text-(--ui-green)`) rather than ignoring it.

- [ ] **Step 7: Update responsive and reduced-motion assertions**

Extend the existing width table test to render collapsible mode and assert:

```tsx
const strip = container.querySelector('[data-layout="collapsible-lanes"]')
expect(strip?.className).toContain('overflow-x-auto')
expect(container.firstElementChild?.className).toContain('min-w-0')
```

Keep the current no-unqualified-animation loop. Every new transition class must also carry `motion-reduce:transition-none`.

- [ ] **Step 8: Run shared board and performance tests**

```bash
cd apps/desktop
npx vitest run src/components/activity-board/lane-collapse.test.ts src/components/activity-board/activity-board.test.tsx src/components/activity-board/activity-board.performance.test.tsx
npx tsc -p . --noEmit
```

Expected: all shared board tests pass, the 1,000-card test mounts fewer than 100 buttons, and renderer type-check exits 0.

- [ ] **Step 9: Commit the presentation change**

```bash
git add apps/desktop/src/components/activity-board
git commit -m "feat(desktop): render collapsible activity lanes"
```

---

### Task 4: Add the localized Workflows board toolbar

**Files:**
- Create: `apps/desktop/src/app/workflows/workflow-view-header.tsx`
- Create: `apps/desktop/src/app/workflows/workflow-view-header.test.tsx`
- Modify: `apps/desktop/src/i18n/types.ts:1676-1815`
- Modify: `apps/desktop/src/i18n/en.ts:2004-2160`
- Modify: `apps/desktop/src/i18n/ar.ts:1660-1680`
- Modify: `apps/desktop/src/i18n/ja.ts:1836-1985`
- Modify: `apps/desktop/src/i18n/zh.ts:2196-2338`
- Modify: `apps/desktop/src/i18n/zh-hant.ts:1777-1920`
- Modify: `apps/desktop/src/app/workflows/index.test.tsx:880-940`

**Interfaces:**
- Consumes: Task 1 disabled `SearchField`, existing `Button`, `Codicon`, `WorkflowRunView`, and Desktop locale catalog.
- Produces: `WorkflowViewHeader`, nine typed localized workflow-board strings/functions, and complete non-English copy.

- [ ] **Step 1: Add typed localization keys**

Add to `Translations['operations']` near `activeBoard`:

```ts
workflowLaneExpand: (lane: string) => string
workflowLaneCollapse: (lane: string) => string
workflowLaneEmpty: string
workflowRunFiltersComingSoon: string
workflowRunSearchComingSoon: string
workflowRunDetailLoading: string
workflowRunDetailError: string
workflowRunInspectorLabel: (workflow: string) => string
workflowLoadedRunCount: (count: number) => string
```

- [ ] **Step 2: Add complete translations**

Add these exact values:

```ts
// en
workflowLaneExpand: lane => `Expand ${lane}`,
workflowLaneCollapse: lane => `Collapse ${lane}`,
workflowLaneEmpty: 'No runs',
workflowRunFiltersComingSoon: 'Run filters coming soon',
workflowRunSearchComingSoon: 'Search runs — coming soon',
workflowRunDetailLoading: 'Loading run details',
workflowRunDetailError: 'Could not load run details',
workflowRunInspectorLabel: workflow => `${workflow} run inspector`,
workflowLoadedRunCount: count => `${count} loaded workflow run${count === 1 ? '' : 's'}`,

// ar
workflowLaneExpand: lane => `توسيع ${lane}`,
workflowLaneCollapse: lane => `طي ${lane}`,
workflowLaneEmpty: 'لا توجد عمليات تشغيل',
workflowRunFiltersComingSoon: 'مرشحات التشغيل قريبًا',
workflowRunSearchComingSoon: 'البحث في عمليات التشغيل — قريبًا',
workflowRunDetailLoading: 'جار تحميل تفاصيل التشغيل',
workflowRunDetailError: 'تعذر تحميل تفاصيل التشغيل',
workflowRunInspectorLabel: workflow => `فاحص تشغيل ${workflow}`,
workflowLoadedRunCount: count => `${count} من عمليات التشغيل المحملة`,

// ja
workflowLaneExpand: lane => `${lane}を展開`,
workflowLaneCollapse: lane => `${lane}を折りたたむ`,
workflowLaneEmpty: '実行はありません',
workflowRunFiltersComingSoon: '実行フィルターは近日対応予定です',
workflowRunSearchComingSoon: '実行を検索 — 近日対応予定',
workflowRunDetailLoading: '実行の詳細を読み込んでいます',
workflowRunDetailError: '実行の詳細を読み込めませんでした',
workflowRunInspectorLabel: workflow => `${workflow} の実行インスペクター`,
workflowLoadedRunCount: count => `読み込み済みの実行 ${count} 件`,

// zh
workflowLaneExpand: lane => `展开${lane}`,
workflowLaneCollapse: lane => `折叠${lane}`,
workflowLaneEmpty: '暂无运行',
workflowRunFiltersComingSoon: '运行筛选即将推出',
workflowRunSearchComingSoon: '搜索运行 — 即将推出',
workflowRunDetailLoading: '正在加载运行详情',
workflowRunDetailError: '无法加载运行详情',
workflowRunInspectorLabel: workflow => `${workflow} 运行检查器`,
workflowLoadedRunCount: count => `已加载 ${count} 个运行`,

// zh-hant
workflowLaneExpand: lane => `展開${lane}`,
workflowLaneCollapse: lane => `收合${lane}`,
workflowLaneEmpty: '沒有執行項目',
workflowRunFiltersComingSoon: '執行篩選即將推出',
workflowRunSearchComingSoon: '搜尋執行 — 即將推出',
workflowRunDetailLoading: '正在載入執行詳細資料',
workflowRunDetailError: '無法載入執行詳細資料',
workflowRunInspectorLabel: workflow => `${workflow} 執行檢查器`,
workflowLoadedRunCount: count => `已載入 ${count} 個執行項目`,
```

- [ ] **Step 3: Extend the locale regression test**

In the existing `catches signal and dependency copy falling back to English in any locale` test, add the five new string keys to `stringKeys`:

```ts
'workflowLaneEmpty',
'workflowRunFiltersComingSoon',
'workflowRunSearchComingSoon',
'workflowRunDetailLoading',
'workflowRunDetailError'
```

Add function assertions inside the locale loop:

```ts
expect(copy.workflowLaneExpand('Lane')).toBeTypeOf('string')
expect(copy.workflowLaneCollapse('Lane')).toBeTypeOf('string')
expect(copy.workflowRunInspectorLabel('Workflow')).toBeTypeOf('string')
expect(copy.workflowLoadedRunCount(2)).toBeTypeOf('string')

if (locale !== 'en') {
  expect(copy.workflowLaneExpand('Lane')).not.toBe(english.workflowLaneExpand('Lane'))
  expect(copy.workflowLaneCollapse('Lane')).not.toBe(english.workflowLaneCollapse('Lane'))
  expect(copy.workflowRunInspectorLabel('Workflow')).not.toBe(english.workflowRunInspectorLabel('Workflow'))
  expect(copy.workflowLoadedRunCount(2)).not.toBe(english.workflowLoadedRunCount(2))
}
```

- [ ] **Step 4: Write failing header tests**

Create `workflow-view-header.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { createRef } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'

import { WorkflowViewHeader } from './workflow-view-header'

afterEach(cleanup)

describe('WorkflowViewHeader', () => {
  it('keeps catalog navigation without run controls', () => {
    render(
      <I18nProvider configClient={null} initialLocale="en">
        <WorkflowViewHeader
          headingRef={createRef<HTMLHeadingElement>()}
          loadedRunCount={0}
          onViewChange={vi.fn()}
          view="workflows"
        />
      </I18nProvider>
    )

    expect(screen.getAllByRole('tab').map(tab => tab.textContent)).toEqual([
      'Workflows',
      'Active board',
      'History',
      'Archive'
    ])
    expect(screen.queryByRole('textbox')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Run filters coming soon' })).toBeNull()
  })

  it('shows an honest disabled run toolbar and dispatches view changes', () => {
    const onViewChange = vi.fn()

    render(
      <I18nProvider configClient={null} initialLocale="en">
        <WorkflowViewHeader
          headingRef={createRef<HTMLHeadingElement>()}
          loadedRunCount={3}
          onViewChange={onViewChange}
          view="board"
        />
      </I18nProvider>
    )

    expect(screen.getByLabelText('3 loaded workflow runs')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Run filters coming soon' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('textbox', { name: 'Search runs — coming soon' }) as HTMLInputElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('tab', { name: 'History' }))
    expect(onViewChange).toHaveBeenCalledWith('history')
  })
})
```

- [ ] **Step 5: Run the header and locale tests and verify failure**

```bash
cd apps/desktop
npx vitest run src/app/workflows/workflow-view-header.test.tsx src/app/workflows/index.test.tsx
```

Expected: FAIL because `WorkflowViewHeader` and the localization keys do not exist.

- [ ] **Step 6: Implement WorkflowViewHeader**

Create `workflow-view-header.tsx` with this interface:

```tsx
import type { RefObject } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { SearchField } from '@/components/ui/search-field'
import { useI18n } from '@/i18n'
import type { WorkflowRunView } from '@/types/hermes'

interface WorkflowViewHeaderProps {
  headingRef: RefObject<HTMLHeadingElement | null>
  loadedRunCount: number
  onViewChange: (view: WorkflowRunView) => void
  view: WorkflowRunView
}

const VIEWS = ['workflows', 'board', 'history', 'archive'] as const

export function WorkflowViewHeader({
  headingRef,
  loadedRunCount,
  onViewChange,
  view
}: WorkflowViewHeaderProps) {
  const { t } = useI18n()
  const copy = t.operations
  const label = (candidate: WorkflowRunView) =>
    candidate === 'workflows'
      ? copy.workflows
      : candidate === 'board'
        ? copy.activeBoard
        : candidate === 'history'
          ? copy.history
          : copy.archive

  return (
    <header className="flex shrink-0 flex-wrap items-center gap-2">
      <h1 className="mr-2 text-lg font-medium" ref={headingRef} tabIndex={-1}>
        {copy.workflows}
      </h1>
      <div aria-label={copy.workflowViews} className="flex flex-wrap gap-2" role="tablist">
        {VIEWS.map(candidate => (
          <Button
            aria-selected={view === candidate}
            key={candidate}
            onClick={() => onViewChange(candidate)}
            role="tab"
            size="sm"
            variant={view === candidate ? 'default' : 'secondary'}
          >
            {label(candidate)}
          </Button>
        ))}
      </div>
      {view !== 'workflows' && (
        <div className="ml-auto flex min-w-0 items-center gap-1.5">
          <span
            aria-label={copy.workflowLoadedRunCount(loadedRunCount)}
            className="rounded-full bg-(--ui-bg-quaternary) px-1.5 py-px text-[0.625rem] tabular-nums text-(--ui-text-tertiary)"
          >
            {loadedRunCount}
          </span>
          <Button aria-label={copy.workflowRunFiltersComingSoon} disabled size="icon-xs" variant="ghost">
            <Codicon name="filter" size="0.85rem" />
          </Button>
          <SearchField
            aria-label={copy.workflowRunSearchComingSoon}
            disabled
            onChange={() => undefined}
            placeholder={copy.workflowRunSearchComingSoon}
            value=""
          />
        </div>
      )}
    </header>
  )
}
```

Do not add component-local filter/search state. The no-op `onChange` exists only because the canonical SearchField callback remains required; native disabled state prevents invocation.

- [ ] **Step 7: Run header, locale, type, and formatting checks**

```bash
cd apps/desktop
npx vitest run src/app/workflows/workflow-view-header.test.tsx src/app/workflows/index.test.tsx src/i18n/runtime.test.ts
npx tsc -p . --noEmit
npx prettier --check src/app/workflows/workflow-view-header.tsx src/app/workflows/workflow-view-header.test.tsx src/i18n/types.ts src/i18n/en.ts src/i18n/ar.ts src/i18n/ja.ts src/i18n/zh.ts src/i18n/zh-hant.ts
```

Expected: tests pass, no locale uses an English fallback for the new keys, type-check exits 0, and formatting check exits 0.

- [ ] **Step 8: Commit the localized toolbar**

```bash
git add apps/desktop/src/app/workflows/workflow-view-header.tsx apps/desktop/src/app/workflows/workflow-view-header.test.tsx apps/desktop/src/app/workflows/index.test.tsx apps/desktop/src/i18n/types.ts apps/desktop/src/i18n/en.ts apps/desktop/src/i18n/ar.ts apps/desktop/src/i18n/ja.ts apps/desktop/src/i18n/zh.ts apps/desktop/src/i18n/zh-hant.ts
git commit -m "feat(desktop): add workflow board toolbar"
```

---

### Task 5: Add the nonmodal workflow run drawer

**Files:**
- Create: `apps/desktop/src/app/workflows/workflow-run-drawer.tsx`
- Create: `apps/desktop/src/app/workflows/workflow-run-drawer.test.tsx`
- Read without modifying: `apps/desktop/src/app/workflows/run-inspector.tsx`

**Interfaces:**
- Consumes: existing `RunInspector`, Task 4 localized loading/error copy, `Loader`, `ErrorState`, `Button`, and workflow public types.
- Produces: `WorkflowRunDrawer` with an absolute nonmodal shell, independent body scrolling, exact Escape dismissal, and run-keyed inspector reset.

- [ ] **Step 1: Write failing drawer shell tests**

Create `workflow-run-drawer.test.tsx`:

```tsx
// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider } from '@/i18n'
import type { WorkflowRunSnapshot } from '@/types/hermes'

import { WorkflowRunDrawer } from './workflow-run-drawer'

vi.mock('./run-inspector', () => ({
  RunInspector: ({ run }: { run: WorkflowRunSnapshot }) => <div>Inspector {run.run_id}</div>
}))

const run: WorkflowRunSnapshot = {
  definition_digest: 'a'.repeat(64),
  health: 'healthy',
  next_actions: [],
  progress: { completed_nodes: 1, kind: 'graph', total_nodes: 2 },
  run_id: 'run-1',
  state_version: 1,
  status: 'running',
  updated_at: '2026-08-09T00:00:00Z',
  workflow: 'Laptop diagnostic'
}

const base = {
  actionsDisabled: false,
  error: null,
  events: [],
  loading: false,
  onAction: vi.fn(),
  onClose: vi.fn(),
  run,
  selectedRunId: 'run-1'
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('WorkflowRunDrawer', () => {
  it('renders a right-side complementary region with the run inspector', () => {
    render(
      <I18nProvider configClient={null} initialLocale="en">
        <WorkflowRunDrawer {...base} />
      </I18nProvider>
    )

    const drawer = screen.getByRole('complementary', { name: 'Laptop diagnostic run inspector' })
    expect(drawer.className).toContain('absolute')
    expect(drawer.className).toContain('right-0')
    expect(screen.getByText('Inspector run-1')).toBeTruthy()
  })

  it('renders bounded loading and error states', () => {
    const view = render(
      <I18nProvider configClient={null} initialLocale="en">
        <WorkflowRunDrawer {...base} loading run={null} />
      </I18nProvider>
    )

    expect(screen.getByLabelText('Loading run details')).toBeTruthy()
    view.rerender(
      <I18nProvider configClient={null} initialLocale="en">
        <WorkflowRunDrawer {...base} error={new Error('detail failed')} run={null} />
      </I18nProvider>
    )
    expect(screen.getByText('Could not load run details')).toBeTruthy()
  })

  it('closes once from the close button or an unhandled Escape', () => {
    const onClose = vi.fn()
    const view = render(
      <I18nProvider configClient={null} initialLocale="en">
        <WorkflowRunDrawer {...base} onClose={onClose} />
      </I18nProvider>
    )

    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(onClose).toHaveBeenCalledTimes(1)
    view.unmount()

    render(
      <I18nProvider configClient={null} initialLocale="en">
        <WorkflowRunDrawer {...base} onClose={onClose} />
      </I18nProvider>
    )
    fireEvent.keyDown(window, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(2)
  })
})
```

- [ ] **Step 2: Run the drawer tests and verify module failure**

```bash
cd apps/desktop
npx vitest run src/app/workflows/workflow-run-drawer.test.tsx
```

Expected: FAIL because `workflow-run-drawer.tsx` does not exist.

- [ ] **Step 3: Implement the drawer shell**

Create `workflow-run-drawer.tsx`:

```tsx
import { useEffect } from 'react'

import { Button } from '@/components/ui/button'
import { Codicon } from '@/components/ui/codicon'
import { ErrorState } from '@/components/ui/error-state'
import { Loader } from '@/components/ui/loader'
import { useI18n } from '@/i18n'
import type { WorkflowRunSnapshot, WorkflowTimelineEvent } from '@/types/hermes'

import { RunInspector } from './run-inspector'

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

export function WorkflowRunDrawer({
  actionsDisabled,
  error,
  events = [],
  loading,
  onAction,
  onClose,
  run,
  selectedRunId
}: WorkflowRunDrawerProps) {
  const { t } = useI18n()
  const copy = t.operations

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || event.defaultPrevented) {
        return
      }

      event.preventDefault()
      event.stopPropagation()
      onClose()
    }

    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const label = copy.workflowRunInspectorLabel(run?.workflow ?? selectedRunId)

  return (
    <aside
      aria-label={label}
      aria-busy={loading && !run}
      className="absolute inset-y-0 right-0 z-20 flex w-full flex-col border-l border-(--ui-stroke-tertiary) bg-(--ui-bg-elevated) sm:w-[min(32rem,calc(100%-2rem))]"
      id="workflow-run-drawer"
    >
      <header className="flex shrink-0 justify-end px-3 pt-3">
        <Button aria-label={t.common.close} onClick={onClose} size="icon-xs" variant="ghost">
          <Codicon name="close" size="0.9rem" />
        </Button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4" data-selectable-text="true">
        {run ? (
          <RunInspector
            actionsDisabled={actionsDisabled}
            events={events}
            key={run.run_id}
            onAction={onAction}
            run={run}
          />
        ) : error ? (
          <ErrorState className="mt-12" title={copy.workflowRunDetailError} />
        ) : (
          <div className="grid h-40 place-items-center">
            <Loader label={copy.workflowRunDetailLoading} type="lemniscate-bloom" />
          </div>
        )}
      </div>
    </aside>
  )
}
```

The presence of stale `run` data wins over `loading`, so a refetch never replaces useful detail with a spinner. Do not add a backdrop, focus trap, dialog role, or drawer-owned query.

- [ ] **Step 4: Run drawer and existing inspector tests**

```bash
cd apps/desktop
npx vitest run src/app/workflows/workflow-run-drawer.test.tsx src/app/workflows/index.test.tsx src/app/workflows/typed-artifact-view.test.tsx
npx tsc -p . --noEmit
```

Expected: drawer tests and all existing inspector/artifact tests pass; renderer type-check exits 0.

- [ ] **Step 5: Commit the drawer**

```bash
git add apps/desktop/src/app/workflows/workflow-run-drawer.tsx apps/desktop/src/app/workflows/workflow-run-drawer.test.tsx
git commit -m "feat(desktop): add workflow run drawer"
```

---

### Task 6: Integrate the toolbar, collapsible board, and drawer into WorkflowsView

**Files:**
- Modify: `apps/desktop/src/app/workflows/index.tsx:50-402`
- Modify: `apps/desktop/src/app/workflows/index.test.tsx:120-1560`

**Interfaces:**
- Consumes: Task 3 collapsible `ActivityBoard`, Task 4 `WorkflowViewHeader` and lane copy, Task 5 `WorkflowRunDrawer`, existing queries/mutations, `$workflowSelectedRunId`, `AttentionInbox`, and evidence cleanup.
- Produces: full-height Workflows composition, direct card-to-drawer activation, focus restoration, view-scoped lane resets, and removal of the below-board inspector.

- [ ] **Step 1: Write failing integration tests for header and board composition**

Add to `index.test.tsx`:

```tsx
it.each([
  ['board', 'Active board'],
  ['history', 'History'],
  ['archive', 'Archive']
] as const)('renders the %s view with collapsible lanes and disabled future controls', async (view, label) => {
  $workflowSelectedRunId.set(null)
  listWorkflowRuns.mockResolvedValue({ next_cursor: null, runs: [run()], schema_version: 1 })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  await renderView(client, 'workflows')
  fireEvent.click(screen.getByRole('tab', { name: label }))

  expect(await screen.findByLabelText('1 loaded workflow run')).toBeTruthy()
  expect(screen.getByLabelText('Workflows activity board').querySelector('[data-layout="collapsible-lanes"]')).toBeTruthy()
  expect((screen.getByRole('button', { name: 'Run filters coming soon' }) as HTMLButtonElement).disabled).toBe(true)
  expect((screen.getByRole('textbox', { name: 'Search runs — coming soon' }) as HTMLInputElement).disabled).toBe(true)
  expect(listWorkflowRuns).toHaveBeenCalledWith(undefined, view)
})

it('keeps the catalog free of run-only toolbar controls', async () => {
  $workflowSelectedRunId.set(null)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  await renderView(client, 'workflows')

  expect(screen.queryByRole('textbox', { name: 'Search runs — coming soon' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'Run filters coming soon' })).toBeNull()
})
```

- [ ] **Step 2: Write failing integration tests for drawer behavior and focus**

```tsx
it('opens selected run detail in a side drawer instead of below the board', async () => {
  $workflowSelectedRunId.set(null)
  getWorkflowRun.mockResolvedValue(run())
  listWorkflowRuns.mockResolvedValue({ next_cursor: null, runs: [run()], schema_version: 1 })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  await renderView(client)
  const card = await screen.findByRole('button', { name: /Laptop diagnostic/ })
  fireEvent.click(card)

  const drawer = await screen.findByRole('complementary', { name: 'Laptop diagnostic run inspector' })
  expect(drawer.className).toContain('absolute')
  expect(drawer.closest('main')).toBeTruthy()
  expect(card.getAttribute('aria-expanded')).toBe('true')
  expect(within(drawer).getByRole('tab', { name: 'Overview' })).toBeTruthy()
  expect(within(drawer).getByRole('tab', { name: 'Timeline events' })).toBeTruthy()
  expect(within(drawer).getByRole('tab', { name: 'Attempts' })).toBeTruthy()
  expect(within(drawer).getByRole('tab', { name: 'Logs' })).toBeTruthy()
  expect(within(drawer).getByRole('tab', { name: 'Outputs' })).toBeTruthy()
  expect(within(drawer).getByRole('tab', { name: 'Verified artifacts' })).toBeTruthy()
  expect(within(drawer).getByRole('tab', { name: 'Recovery' })).toBeTruthy()
})

it('closes the drawer, clears selected polling, and restores card focus', async () => {
  $workflowSelectedRunId.set(null)
  getWorkflowRun.mockResolvedValue(run())
  listWorkflowRuns.mockResolvedValue({ next_cursor: null, runs: [run()], schema_version: 1 })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  await renderView(client)
  const card = await screen.findByRole('button', { name: /Laptop diagnostic/ })
  fireEvent.click(card)
  fireEvent.click(await screen.findByRole('button', { name: 'Close' }))

  await waitFor(() => expect($workflowSelectedRunId.get()).toBeNull())
  await waitFor(() => expect(document.activeElement).toBe(card))
  expect(screen.queryByRole('complementary')).toBeNull()
})

it('keeps a selected-run failure inside a closeable drawer', async () => {
  $workflowSelectedRunId.set(null)
  getWorkflowRun.mockRejectedValue(new Error('detail failed'))
  listWorkflowRuns.mockResolvedValue({ next_cursor: null, runs: [run()], schema_version: 1 })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  await renderView(client)
  fireEvent.click(await screen.findByRole('button', { name: /Laptop diagnostic/ }))

  const drawer = await screen.findByRole('complementary')
  expect(within(drawer).getByText('Could not load run details')).toBeTruthy()
  expect(screen.getByRole('button', { name: /Laptop diagnostic/ })).toBeTruthy()
  fireEvent.click(within(drawer).getByRole('button', { name: 'Close' }))
  expect(screen.queryByRole('complementary')).toBeNull()
})
```

- [ ] **Step 3: Write failing tests for view changes and inspector reset**

```tsx
it('closes selected detail without stealing focus when navigation changes', async () => {
  getWorkflowRun.mockResolvedValue(run())
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  await renderView(client)
  expect(await screen.findByRole('complementary')).toBeTruthy()
  const history = screen.getByRole('tab', { name: 'History' })
  history.focus()
  fireEvent.click(history)

  expect($workflowSelectedRunId.get()).toBeNull()
  expect(screen.queryByRole('complementary')).toBeNull()
  expect(document.activeElement).toBe(history)
})

it('resets the inspector to Overview when another run is selected', async () => {
  const runTwo = run({ run_id: 'run-2', workflow: 'Second workflow' })
  $workflowSelectedRunId.set(null)
  listWorkflowRuns.mockResolvedValue({ next_cursor: null, runs: [run(), runTwo], schema_version: 1 })
  getWorkflowRun.mockImplementation((id: string) => Promise.resolve(id === 'run-2' ? runTwo : run()))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  await renderView(client)
  fireEvent.click(await screen.findByRole('button', { name: /Laptop diagnostic/ }))
  fireEvent.mouseDown(await screen.findByRole('tab', { name: 'Logs' }), { button: 0, ctrlKey: false })
  fireEvent.click(screen.getByRole('button', { name: /Second workflow/ }))

  await waitFor(() => expect(screen.getByRole('tab', { name: 'Overview' }).getAttribute('data-state')).toBe('active'))
})
```

- [ ] **Step 4: Run the new integration tests and verify failure**

```bash
cd apps/desktop
npx vitest run src/app/workflows/index.test.tsx
```

Expected: new tests fail because `WorkflowsView` still renders the old header/grid and appends `RunInspector` below the board.

- [ ] **Step 5: Add feature-owned focus-return refs and handlers**

In `WorkflowsView`, add:

```tsx
const headingRef = useRef<HTMLHeadingElement>(null)
const runReturnFocusRef = useRef<HTMLButtonElement | null>(null)

const openRun = (card: ActivityBoardCard, origin?: HTMLButtonElement) => {
  runReturnFocusRef.current = origin ?? null
  selectWorkflowRun(card.id)
}

const closeRunDrawer = () => {
  const target = runReturnFocusRef.current
  runReturnFocusRef.current = null
  selectWorkflowRun(null)

  requestAnimationFrame(() => {
    if (target?.isConnected) {
      target.focus()
    } else {
      headingRef.current?.focus()
    }
  })
}

const changeView = (next: WorkflowRunView) => {
  runReturnFocusRef.current = null
  selectWorkflowRun(null)
  setView(next)
}
```

Import `ActivityBoardCard` and `WorkflowRunView` as types. View navigation must call `changeView`; it must not call the focus-restoring drawer close handler.

- [ ] **Step 6: Replace the page heading/tabs with WorkflowViewHeader**

Calculate the loaded count from the board model:

```tsx
const loadedRunCount = model.columns.reduce((total, column) => total + column.cards.length, 0)
```

Replace the current `<h1>` and tablist with:

```tsx
<WorkflowViewHeader
  headingRef={headingRef}
  loadedRunCount={loadedRunCount}
  onViewChange={changeView}
  view={view}
/>
```

Use a full-height page root:

```tsx
<main
  aria-busy={busyExpression}
  className={`relative flex h-full min-h-0 min-w-0 flex-col overflow-hidden py-6 ${PAGE_INSET_X}`}
>
```

Keep the existing `aria-busy` expression; do not replace it with a new derived source of truth.

- [ ] **Step 7: Render the collapsible board with localized lane copy**

Inside the run-view content region, pass:

```tsx
<ActivityBoard
  collapseScope={view}
  laneCopy={{
    collapse: t.operations.workflowLaneCollapse,
    empty: t.operations.workflowLaneEmpty,
    expand: t.operations.workflowLaneExpand
  }}
  layout="collapsible-lanes"
  model={model}
  onLoadMore={() => void runs.fetchNextPage()}
  onOpenCard={openRun}
  selectedCardId={selectedRunId}
/>
```

Wrap Attention inbox, board, and cleanup sections in one `min-h-0 flex-1 overflow-y-auto` content region. Preserve Attention before the board and preserve the complete existing cleanup JSX after the board for History and Archive.

- [ ] **Step 8: Replace the below-board inspector with WorkflowRunDrawer**

Delete the current conditional `selected.data && <RunInspector ... />`. Add this page-root sibling after the scrollable content and before the View/Review dialogs:

```tsx
{view !== 'workflows' && selectedRunId ? (
  <WorkflowRunDrawer
    actionsDisabled={actionPending || mutation.isPending || selected.isError}
    error={selected.error}
    events={events.data?.events}
    loading={selected.isLoading}
    onAction={mutateRun}
    onClose={closeRunDrawer}
    run={selected.data ?? null}
    selectedRunId={selectedRunId}
  />
) : null}
```

Keep the existing selected query and event query keys, enablement, intervals, cancellation effect, mutation code, and invalidation code unchanged.

- [ ] **Step 9: Run the complete workflow renderer suite**

```bash
cd apps/desktop
npx vitest run src/components/ui/search-field.test.tsx src/components/activity-board/lane-collapse.test.ts src/components/activity-board/activity-board.test.tsx src/components/activity-board/activity-board.performance.test.tsx src/app/workflows/workflow-view-header.test.tsx src/app/workflows/workflow-run-drawer.test.tsx src/app/workflows/index.test.tsx src/app/workflows/workflow-operations.e2e.test.tsx src/app/workflows/typed-artifact-view.test.tsx src/app/workflows/review-run-dialog.test.tsx src/app/workflows/view-workflow-dialog.test.tsx
```

Expected: all focused workflow and shared-board tests pass. Existing tests continue proving 409 recovery, evidence isolation, action behavior, archive/cleanup separation, profile routing, and typed-artifact handling.

- [ ] **Step 10: Run Desktop workflow gate, type-check, lint, and diff checks**

```bash
cd apps/desktop
npm run test:workflow-ui
npm run typecheck
npm run lint
cd ../..
git diff --check
git status --short
```

Expected:

- workflow UI suite passes;
- all renderer, Electron, and E2E TypeScript projects type-check;
- ESLint exits 0;
- `git diff --check` prints nothing;
- status contains only this task's intended files plus pre-existing unrelated user files.

- [ ] **Step 11: Commit the Workflows integration**

```bash
git add apps/desktop/src/app/workflows/index.tsx apps/desktop/src/app/workflows/index.test.tsx
git diff --cached --check
git commit -m "feat(desktop): align workflow boards with kanban lanes"
```

Verify `git diff --cached --name-only` before committing so unrelated working-tree files remain untouched. `run-inspector.tsx` must remain unchanged; the new drawer composes it through its existing public props.

## Final acceptance checklist

- [ ] Active board, History, and Archive render fixed 16rem lanes with automatic 2rem empty rails.
- [ ] Mouse and keyboard can expand/collapse lanes without changing server state or card membership.
- [ ] The loaded-run count is honest under pagination.
- [ ] Filter and search controls are visible, localized, native-disabled, and produce no state/query change.
- [ ] No profile/filter API or UI logic was introduced.
- [ ] A card opens the existing seven-tab inspector in the right-side nonmodal drawer.
- [ ] Loading and selected-detail failure stay inside a closeable drawer.
- [ ] Close/Escape clears selected identity and returns focus; view navigation keeps focus on the chosen tab.
- [ ] Background refetches never select a run or open the drawer.
- [ ] Attention inbox and explicit History/Archive cleanup remain semantically unchanged.
- [ ] Large columns remain virtualized and horizontal scrolling stays inside the lane strip.
- [ ] New motion respects reduced-motion preferences.
- [ ] Every new string is complete in `en`, `ar`, `ja`, `zh`, and `zh-hant`.
- [ ] No Python, backend, plugin, SDK Kanban, Electron main-process, dependency, config, or environment-variable change exists.
- [ ] Focused tests, `npm run test:workflow-ui`, `npm run typecheck`, `npm run lint`, and `git diff --check` all pass.
