import { describe, expect, it } from 'vitest'

import {
  decodeWorkflowAttentionPage,
  decodeWorkflowEventPage,
  decodeWorkflowEvidencePage,
  decodeWorkflowRun,
  decodeWorkflowRunPage,
  formatWorkflowEvidenceItem
} from './workflow-public-codec'

const run = {
  action: 'status',
  artifacts: [],
  attempts: 0,
  coordinator: null,
  current_nodes: ['work'],
  health: 'healthy',
  next_actions: ['cancel'],
  nodes: {},
  pending_interaction: null,
  progress: { completed_nodes: 0, kind: 'graph', total_nodes: 1 },
  provenance: null,
  queue_position: null,
  run_id: 'run-1',
  schema_version: 1,
  state_version: 2,
  status: 'running',
  status_authoritative: true,
  updated_at: '2026-08-08T20:00:00Z',
  workflow: 'portable'
}

describe('workflow public codecs', () => {
  it('accepts only closed run and run-page objects', () => {
    expect(decodeWorkflowRun(run)).toEqual(run)
    expect(decodeWorkflowRunPage({ next_cursor: null, runs: [run], schema_version: 1 })).toEqual({
      next_cursor: null,
      runs: [run],
      schema_version: 1
    })

    expect(decodeWorkflowRun({ ...run, provider_payload: 'private' })).toBeNull()
    expect(
      decodeWorkflowRun({
        ...run,
        coordinator: { provider_payload: 'private', status: 'healthy' }
      })
    ).toBeNull()
    expect(
      decodeWorkflowRun({
        ...run,
        provenance: { assurance: 'verified_adapter', source: 'desktop', token: 'private' }
      })
    ).toBeNull()
    expect(
      decodeWorkflowRun({
        ...run,
        pending_interaction: {
          interaction_id: 'approval-1',
          message: 'rendered private approval',
          node_id: 'work',
          type: 'workflow_approval'
        }
      })
    ).toBeNull()
  })

  it('rejects event payloads and evidence extras at runtime', () => {
    const event = {
      actor: 'operator-1',
      channel: 'desktop',
      event_type: 'interaction_approved',
      item_type: 'timeline_event',
      node_id: 'work',
      run_id: 'run-1',
      sequence: 3,
      timestamp: '2026-08-08T20:00:00Z'
    }

    expect(
      decodeWorkflowEventPage({ cursor_reset: false, events: [event], next_cursor: 3, schema_version: 1 })
    ).not.toBeNull()
    expect(
      decodeWorkflowEventPage({
        cursor_reset: false,
        events: [{ ...event, payload: { provider_payload: 'private' } }],
        next_cursor: 3,
        schema_version: 1
      })
    ).toBeNull()

    const interactionPage = {
      items: [
        {
          actor: 'operator-1',
          channel: 'desktop',
          event_type: 'interaction_approved',
          item_type: 'interaction',
          node_id: 'work',
          sequence: 4
        }
      ],
      kind: 'interactions',
      next_cursor: 1,
      schema_version: 1,
      truncated: false
    }

    expect(decodeWorkflowEvidencePage(interactionPage)).toEqual(interactionPage)
    expect(
      decodeWorkflowEvidencePage({
        ...interactionPage,
        items: [{ ...interactionPage.items[0], comment: 'private' }]
      })
    ).toBeNull()

    const attempt = {
      attempt_id: 'attempt-1',
      error: { code: 'workflow_operation_failed', message: 'Workflow operation failed.' },
      item_type: 'attempt',
      node_id: 'work',
      retry: {
        additional_provider_attempts: 0,
        capped: false,
        effective_total_attempts: 1,
        remaining_attempts: 0,
        requested_retries: 0,
        requested_total_attempts: 1,
        retry_consumed: 1
      },
      state: 'failed'
    }

    const page = {
      items: [attempt],
      kind: 'attempts',
      next_cursor: 1,
      schema_version: 1,
      truncated: false
    }

    expect(decodeWorkflowEvidencePage(page)).toEqual(page)
    expect(
      decodeWorkflowEvidencePage({
        ...page,
        items: [{ ...attempt, audit: { error: 'private' } }]
      })
    ).toBeNull()
  })

  it('formats decoded evidence without serializing arbitrary objects', () => {
    const page = decodeWorkflowEvidencePage({
      items: [
        {
          attempt_id: 'attempt-1',
          error: { code: 'workflow_operation_failed', message: 'Workflow operation failed.' },
          item_type: 'attempt',
          node_id: 'work',
          retry: {
            additional_provider_attempts: 0,
            capped: false,
            effective_total_attempts: 1,
            remaining_attempts: 0,
            requested_retries: 0,
            requested_total_attempts: 1,
            retry_consumed: 1
          },
          state: 'failed'
        }
      ],
      kind: 'attempts',
      next_cursor: 1,
      schema_version: 1,
      truncated: false
    })

    expect(page).not.toBeNull()
    expect(formatWorkflowEvidenceItem(page!.items[0])).toBe(
      'work · attempt-1 · failed · workflow_operation_failed: Workflow operation failed.'
    )
  })

  it('rejects rendered interaction text in attention items', () => {
    const item = {
      cause: 'workflow_approval',
      health: 'user_wait',
      interaction: {
        interaction_id: 'approval-1',
        node_id: 'work',
        type: 'workflow_approval'
      },
      kind: 'workflow_approval',
      next_actions: ['status', 'events', 'approve', 'reject', 'cancel'],
      node_id: 'work',
      origin: 'desktop',
      run_id: 'run-1',
      state_version: 2,
      status: 'paused',
      updated_at: '2026-08-08T20:00:00Z',
      workflow: 'portable'
    }

    expect(decodeWorkflowAttentionPage({ items: [item], next_cursor: null, schema_version: 1 })).not.toBeNull()
    expect(
      decodeWorkflowAttentionPage({
        items: [{ ...item, interaction: { ...item.interaction, message: 'private' } }],
        next_cursor: null,
        schema_version: 1
      })
    ).toBeNull()
  })
})
