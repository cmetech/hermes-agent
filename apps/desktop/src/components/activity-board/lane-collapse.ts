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
