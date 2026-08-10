import { describe, expect, expectTypeOf, it } from 'vitest'

import type { WorkflowRunSnapshot, WorkflowRunStatus } from '@/types/hermes'

import { workflowBoardModel } from './adapter'

const run = (status: WorkflowRunStatus): WorkflowRunSnapshot => ({
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
    const statuses = ['queued', 'running', 'paused', 'succeeded', 'failed'] satisfies WorkflowRunStatus[]
    const model = workflowBoardModel(statuses.map(run))
    expect(model.source).toBe('workflow')
    expect(model.columns.map(column => column.cards[0]?.exactState)).toEqual([
      'queued',
      'running',
      'paused',
      'succeeded',
      'failed'
    ])
    expect(model.columns.map(column => [column.id, column.tone])).toEqual([
      ['queued', 'var(--ui-blue)'],
      ['active', 'var(--ui-green)'],
      ['attention', 'var(--ui-yellow)'],
      ['completed', 'var(--ui-text-tertiary)'],
      ['stopped', 'var(--ui-red)']
    ])
    expect(model.columns[3]!.cards[0]!.badges[0]!.label).toBe('2/2')
  })

  it('derives origin and coordinator attention from durable server fields', () => {
    const model = workflowBoardModel([
      run('running'),
      {
        ...run('running'),
        coordinator: { reason_code: 'leader_lease_expired', status: 'unavailable' },
        current_nodes: ['publish'],
        health: 'coordinator_unavailable',
        provenance: { assurance: 'verified_adapter', source: 'desktop' },
        status: 'running'
      }
    ])

    const attention = model.columns.find(column => column.id === 'attention')!
    const card = attention.cards[0]!

    expect(card.badges).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ icon: 'device-desktop', label: 'desktop' }),
        expect.objectContaining({ label: 'coordinator unavailable', tone: 'danger' }),
        expect.objectContaining({ label: 'publish' })
      ])
    )
    expect(card.ariaDescription).toContain('verified_adapter')
  })

  it('uses the localized scheduled state in both visible and accessible card copy', () => {
    const model = workflowBoardModel(
      [
        {
          ...run('queued'),
          presentation_state: 'scheduled_wait',
          schedule_at: '2099-01-02T03:04:05Z',
          workflow: 'Deferred deployment'
        }
      ],
      { scheduledLabel: 'スケジュール済み', scopeLabel: 'ワークフロー' }
    )

    const card = model.columns[0]!.cards[0]!

    expect(card.exactState).toBe('スケジュール済み')
    expect(card.ariaDescription).toBe('Deferred deployment, スケジュール済み, healthy')
  })

  it('types nullable server scheduling projection fields explicitly', () => {
    expectTypeOf<WorkflowRunSnapshot['presentation_state']>().toEqualTypeOf<null | string | undefined>()
    expectTypeOf<WorkflowRunSnapshot['schedule_at']>().toEqualTypeOf<null | string | undefined>()
  })
})
