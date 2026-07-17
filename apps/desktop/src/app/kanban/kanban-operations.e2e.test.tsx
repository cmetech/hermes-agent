import { expect, it } from 'vitest'

import type { KanbanBoardSummary } from '@/types/hermes'

import { kanbanBoardModel } from './adapter'

it('never projects a workflow run as a Kanban task or generic card move', () => {
  const summary: KanbanBoardSummary = {
    assignees: [], board: 'physical-project', column_counts: { ready: 1, running: 0 },
    diagnostics_count: 0, latest_event_id: 8, oldest_event_id: 1, schema_version: 1, tenants: []
  }

  const model = kanbanBoardModel(summary, [
    { id: 'task-1', priority: 1, status: 'ready', title: 'Physical task' }
  ])

  expect(model.source).toBe('kanban')
  expect(model.scopeLabel).toBe('Kanban: physical-project')
  expect(model.columns.flatMap(column => column.cards).map(card => card.id)).toEqual(['task-1'])
  expect('onMoveCard' in model).toBe(false)
})
