import { describe, expect, it } from 'vitest'

import type { WorkflowRunSnapshot } from '@/types/hermes'

import { workflowBoardModel } from './adapter'

describe('workflow operations lifecycle authority', () => {
  it('keeps exact workflow states and immutable run identity in the workflow model', () => {
    const states = ['queued', 'running', 'waiting_retry', 'paused', 'failed', 'succeeded']

    const runs: WorkflowRunSnapshot[] = states.map((status, index) => ({
      definition_digest: `definition-${index}`,
      health: status === 'paused' ? 'user_wait' : status === 'waiting_retry' ? 'retry_wait' : 'healthy',
      next_actions: ['status'],
      progress: { completed_nodes: index, kind: 'graph', total_nodes: states.length },
      run_id: `run-${index}`,
      state_version: index + 1,
      status,
      updated_at: '2026-07-17T00:00:00Z',
      workflow: 'Portable contract'
    }))

    const model = workflowBoardModel(runs)
    expect(model.source).toBe('workflow')
    expect(model.columns.flatMap(column => column.cards).map(card => card.id)).toHaveLength(states.length)
    expect(model.columns.flatMap(column => column.cards).map(card => card.exactState)).toEqual(expect.arrayContaining(states))
  })
})
