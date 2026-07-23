// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { WorkflowRunSnapshot } from '@/types/hermes'

import { $workflowSelectedRunId } from './store'

import { WorkflowsView } from './index'

type ApiRequest = Parameters<Window['hermesDesktop']['api']>[0]

function snapshot(overrides: Partial<WorkflowRunSnapshot> = {}): WorkflowRunSnapshot {
  return {
    definition_digest: 'definition-1',
    health: 'user_wait',
    next_actions: ['approve'],
    pending_interaction: { interaction_id: 'interaction-1', type: 'workflow_approval' },
    progress: { completed_nodes: 0, kind: 'graph', total_nodes: 1 },
    run_id: 'run-1',
    state_version: 1,
    status: 'paused',
    updated_at: '2026-07-19T00:00:00Z',
    workflow: 'Portable contract',
    ...overrides
  }
}

describe('workflow operations mounted adapter flow', () => {
  const api = vi.fn()
  const apiStructured = vi.fn()

  beforeEach(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' })
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: { api, apiStructured }
    })
    apiStructured.mockResolvedValue({ ok: true, value: { items: [], truncated: false } })
    $workflowSelectedRunId.set('run-1')
  })

  afterEach(() => {
    cleanup()
    api.mockReset()
    apiStructured.mockReset()
    $workflowSelectedRunId.set(null)
    Reflect.deleteProperty(window, 'hermesDesktop')
  })

  it('constructs a mutation, refreshes a conflict, and repaints from authoritative REST state', async () => {
    let current = snapshot()
    api.mockImplementation(async (request: ApiRequest) => {
      if (request.path.startsWith('/api/plugins/workflow/runs?')) {
        return { next_cursor: null, runs: [current], schema_version: 1 }
      }

      if (request.path === '/api/plugins/workflow/attention') {
        return { items: [], next_cursor: null, schema_version: 1 }
      }

      if (request.path.includes('/events?')) {
        return { cursor_reset: false, events: [], next_cursor: 0, schema_version: 1 }
      }

      if (request.path === '/api/plugins/workflow/runs/run-1/approve') {
        current = snapshot({
          next_actions: ['cancel'],
          pending_interaction: null,
          state_version: 2,
          status: 'running'
        })
        throw Object.assign(new Error('409: stale state'), { statusCode: 409 })
      }

      if (request.path === '/api/plugins/workflow/runs/run-1') {
        return current
      }

      throw new Error(`unexpected workflow request: ${request.path}`)
    })

    const client = new QueryClient({
      defaultOptions: { mutations: { retry: false }, queries: { retry: false } }
    })

    render(
      <QueryClientProvider client={client}>
        <WorkflowsView />
      </QueryClientProvider>
    )
    fireEvent.click(await screen.findByRole('tab', { name: 'Active board' }))
    $workflowSelectedRunId.set('run-1')
    fireEvent.click(await screen.findByRole('button', { name: 'Approve' }))

    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        expect.objectContaining({
          body: { expected_version: 1, interaction_id: 'interaction-1' },
          method: 'POST',
          path: '/api/plugins/workflow/runs/run-1/approve'
        })
      )
    )
    const cancel = await screen.findByRole('button', { name: 'Cancel' })
    expect((cancel as HTMLButtonElement).disabled).toBe(false)
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull()
  })

  it('cancels a scheduled wait through the existing mutation and repaints terminal state', async () => {
    const scheduleAt = '2099-01-02T03:04:05Z'
    let current = snapshot({
      blocking_reason: 'scheduled_wait',
      next_actions: ['cancel'],
      pending_interaction: null,
      presentation_state: 'scheduled_wait',
      schedule_at: scheduleAt,
      status: 'queued'
    })
    api.mockImplementation(async (request: ApiRequest) => {
      if (request.path.startsWith('/api/plugins/workflow/runs?')) {
        return { next_cursor: null, runs: [current], schema_version: 1 }
      }
      if (request.path === '/api/plugins/workflow/attention') {
        return { items: [], next_cursor: null, schema_version: 1 }
      }
      if (request.path.includes('/events?')) {
        return { cursor_reset: false, events: [], next_cursor: 0, schema_version: 1 }
      }
      if (request.path === '/api/plugins/workflow/runs/run-1/cancel') {
        current = snapshot({
          blocking_reason: null,
          next_actions: [],
          pending_interaction: null,
          presentation_state: undefined,
          schedule_at: scheduleAt,
          state_version: 2,
          status: 'cancelled'
        })
        return current
      }
      if (request.path === '/api/plugins/workflow/runs/run-1') {
        return current
      }
      throw new Error(`unexpected workflow request: ${request.path}`)
    })

    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })}
      >
        <WorkflowsView />
      </QueryClientProvider>
    )
    fireEvent.click(await screen.findByRole('tab', { name: 'Active board' }))
    $workflowSelectedRunId.set('run-1')
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))

    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        expect.objectContaining({
          body: { expected_version: 1 },
          method: 'POST',
          path: '/api/plugins/workflow/runs/run-1/cancel'
        })
      )
    )
    expect(await screen.findAllByText('cancelled')).toHaveLength(2)
    expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull()
  })
})
