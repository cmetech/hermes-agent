import { describe, expect, it } from 'vitest'

import { laneIsCollapsed, reconcileLaneCollapseState, toggleLaneCollapse } from './lane-collapse'
import type { ActivityBoardColumn } from './types'

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

    expect(laneIsCollapsed(state, columns[0]!)).toBe(true)
    expect(laneIsCollapsed(state, columns[1]!)).toBe(false)
  })

  it('auto-collapses every empty lane when the whole board is empty', () => {
    const columns = [column('queued', 0), column('active', 0)]
    const state = reconcileLaneCollapseState(null, 'board', columns)

    expect(columns.map(item => laneIsCollapsed(state, item))).toEqual([true, true])
  })

  it('stores only deviations from automatic state', () => {
    const columns = [column('queued', 0), column('active', 1)]
    const initial = reconcileLaneCollapseState(null, 'board', columns)
    const expandedEmpty = toggleLaneCollapse(initial, columns[0]!)
    const collapsedOccupied = toggleLaneCollapse(expandedEmpty, columns[1]!)

    expect(expandedEmpty.overrides).toEqual({ queued: false })
    expect(collapsedOccupied.overrides).toEqual({ active: true, queued: false })
    expect(toggleLaneCollapse(collapsedOccupied, columns[1]!).overrides).toEqual({ queued: false })
  })

  it('drops stale overrides when occupancy or scope changes', () => {
    const before = [column('queued', 0), column('active', 1)]
    let state = reconcileLaneCollapseState(null, 'board', before)
    state = toggleLaneCollapse(state, before[0]!)
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
