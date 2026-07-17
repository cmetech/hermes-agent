import { expect, it } from 'vitest'

import { workflowBoardModel } from '@/app/workflows/adapter'
import type { WorkflowRunSnapshot } from '@/types/hermes'

it('projects one thousand cards in bounded linear presentation work', () => {
  const runs: WorkflowRunSnapshot[] = Array.from({ length: 1000 }, (_, index) => ({
    health: 'healthy', next_actions: ['status'],
    progress: { completed_nodes: index % 4, kind: 'graph', total_nodes: 4 },
    run_id: `run-${index}`, state_version: 1, status: index % 2 ? 'running' : 'succeeded',
    updated_at: '2026-07-17T00:00:00Z', workflow: `Workflow ${index}`
  }))

  const started = performance.now()
  const model = workflowBoardModel(runs)
  const elapsed = performance.now() - started

  expect(model.columns.reduce((total, column) => total + column.cards.length, 0)).toBe(1000)
  expect(elapsed).toBeLessThan(1000)
})
