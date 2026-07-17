import { expect, it } from 'vitest'

import type { KanbanBoardSummary } from '@/types/hermes'

import { kanbanBoardModel } from './adapter'

it('keeps physical Kanban task states separate from workflow runs', () => {
  const summary: KanbanBoardSummary = {
    assignees: [],
    board: 'default',
    column_counts: { ready: 1 },
    diagnostics_count: 0,
    latest_event_id: 4,
    oldest_event_id: 1,
    schema_version: 1,
    tenants: []
  }

  const model = kanbanBoardModel(summary, [{ id: 't_1', priority: 1, status: 'ready', title: 'Task' }])
  expect(model.source).toBe('kanban')
  expect(model.columns[0]!.cards[0]!.id).toBe('t_1')
  expect(model.columns[0]!.cards[0]!.exactState).toBe('ready')
})
