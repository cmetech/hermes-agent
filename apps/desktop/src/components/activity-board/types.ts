export interface ActivityBadge {
  icon?: string
  label: string
  tone?: 'danger' | 'muted' | 'notice' | 'success'
}

export interface ActivityBoardCard {
  ariaDescription: string
  badges: readonly ActivityBadge[]
  exactState: string
  health: 'attention' | 'failed' | 'healthy' | 'idle' | 'stale' | 'terminal' | 'waiting'
  id: string
  title: string
  updatedAt: number
}

export interface ActivityBoardColumn {
  cards: readonly ActivityBoardCard[]
  count: number
  id: string
  label: string
  nextCursor: null | string
}

export interface ActivityBoardModel {
  columns: readonly ActivityBoardColumn[]
  revision: string
  scopeLabel: string
  source: 'kanban' | 'workflow'
  stale: boolean
}

export interface ActivityBoardLaneCopy {
  collapse: (lane: string) => string
  empty: string
  expand: (lane: string) => string
}
