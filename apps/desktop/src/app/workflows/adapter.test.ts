import { describe, expect, it } from 'vitest'

import type { WorkflowRunSnapshot } from '@/types/hermes'

import { workflowBoardModel } from './adapter'

const run = (status: string): WorkflowRunSnapshot => ({
  health: status === 'paused' ? 'user_wait' : 'healthy',
  next_actions: ['status'],
  progress: { completed_nodes: status === 'succeeded' ? 2 : 1, kind: 'graph', total_nodes: 2 },
  run_id: status,
  state_version: 1,
  status,
  updated_at: '2026-07-17T00:00:00Z',
  workflow: `Workflow ${status}`
})

describe('workflowBoardModel', () => {
  it('keeps lifecycle authority in exact states while grouping for presentation', () => {
    const model = workflowBoardModel(['queued', 'running', 'paused', 'succeeded', 'failed'].map(run))
    expect(model.source).toBe('workflow')
    expect(model.columns.map(column => column.cards[0]?.exactState)).toEqual([
      'queued',
      'running',
      'paused',
      'succeeded',
      'failed'
    ])
    expect(model.columns[3]!.cards[0]!.badges[0]!.label).toBe('2/2')
  })
})
