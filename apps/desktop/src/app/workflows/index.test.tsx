// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { WorkflowRunSnapshot } from '@/types/hermes'

import { $workflowSelectedRunId } from './store'

const getWorkflowRun = vi.fn()
const listWorkflowAttention = vi.fn()
const listWorkflowEvents = vi.fn()
const listWorkflowRuns = vi.fn()
const mutateWorkflowRun = vi.fn()

vi.mock('@/hermes', () => ({
  getApiRequestProfile: () => 'default',
  getWorkflowRun: (...args: unknown[]) => getWorkflowRun(...args),
  listWorkflowAttention: (...args: unknown[]) => listWorkflowAttention(...args),
  listWorkflowEvents: (...args: unknown[]) => listWorkflowEvents(...args),
  listWorkflowRuns: (...args: unknown[]) => listWorkflowRuns(...args),
  mutateWorkflowRun: (...args: unknown[]) => mutateWorkflowRun(...args)
}))

function deferred<T>() {
  let reject!: (reason?: unknown) => void
  let resolve!: (value: T) => void

  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve
    reject = onReject
  })

  return { promise, reject, resolve }
}

function run(overrides: Partial<WorkflowRunSnapshot> = {}): WorkflowRunSnapshot {
  return {
    definition_digest: 'definition-1',
    health: 'user_wait',
    next_actions: ['approve'],
    pending_interaction: { interaction_id: 'interaction-1', type: 'workflow_approval' },
    progress: { completed_nodes: 1, kind: 'graph', total_nodes: 2 },
    run_id: 'run-1',
    state_version: 1,
    status: 'paused',
    updated_at: '2026-07-17T00:00:00Z',
    workflow: 'Laptop diagnostic',
    ...overrides
  }
}

function renderView(client: QueryClient) {
  return import('./index').then(({ WorkflowsView }) => render(
    <QueryClientProvider client={client}>
      <WorkflowsView />
    </QueryClientProvider>
  ))
}

beforeEach(() => {
  for (const mock of [getWorkflowRun, listWorkflowAttention, listWorkflowEvents, listWorkflowRuns, mutateWorkflowRun]) {
    mock.mockReset()
  }

  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
  $workflowSelectedRunId.set('run-1')
  getWorkflowRun.mockResolvedValue(run())
  listWorkflowAttention.mockResolvedValue({ items: [], next_cursor: null, schema_version: 1 })
  listWorkflowEvents.mockResolvedValue({ cursor_reset: false, events: [], next_cursor: 0, schema_version: 1 })
  listWorkflowRuns.mockResolvedValue({ next_cursor: null, runs: [run()], schema_version: 1 })
})

afterEach(() => {
  cleanup()
  $workflowSelectedRunId.set(null)
  vi.clearAllMocks()
})

describe('WorkflowsView', () => {
  it('refetches a stale run after 409 and disables repeat actions until recovery', async () => {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })
    const mutation = deferred<WorkflowRunSnapshot>()
    const refreshed = deferred<WorkflowRunSnapshot>()
    getWorkflowRun
      .mockResolvedValueOnce(run())
      .mockImplementationOnce(() => refreshed.promise)
    mutateWorkflowRun.mockImplementationOnce(() => mutation.promise)
    await renderView(client)
    const approve = await screen.findByRole('button', { name: 'Approve' })

    fireEvent.click(approve)
    fireEvent.click(approve)

    expect((approve as HTMLButtonElement).disabled).toBe(true)
    await waitFor(() => expect(mutateWorkflowRun).toHaveBeenCalledTimes(1))
    mutation.reject(Object.assign(new Error('409: stale state version'), { statusCode: 409 }))
    await waitFor(() => expect(getWorkflowRun).toHaveBeenCalledTimes(2))
    expect((approve as HTMLButtonElement).disabled).toBe(true)
    refreshed.resolve(run({ next_actions: ['cancel'], state_version: 2 }))

    const cancel = await screen.findByRole('button', { name: 'Cancel' })
    expect((cancel as HTMLButtonElement).disabled).toBe(false)
    expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull()
  })

  it('replaces event history when the backend reports a cursor gap', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    listWorkflowEvents
      .mockResolvedValueOnce({ cursor_reset: false, events: [{ sequence: 1 }, { sequence: 2 }], next_cursor: 2, schema_version: 1 })
      .mockResolvedValueOnce({ cursor_reset: true, events: [{ sequence: 10 }], next_cursor: 10, schema_version: 1 })
    await renderView(client)
    const timeline = await screen.findByText('Timeline events')
    await waitFor(() => expect(timeline.nextElementSibling?.textContent).toBe('2'))

    await client.refetchQueries({ queryKey: ['workflow-events', 'default', 'run-1'] })

    await waitFor(() => expect(timeline.nextElementSibling?.textContent).toBe('1'))
  })

  it('disables lifecycle actions while the selected snapshot is disconnected', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } })
    client.setQueryData(['workflow-run', 'default', 'run-1'], run())
    getWorkflowRun.mockRejectedValueOnce(new Error('gateway disconnected'))

    await renderView(client)

    const approve = await screen.findByRole('button', { name: 'Approve' })
    await waitFor(() => expect((approve as HTMLButtonElement).disabled).toBe(true))
    fireEvent.click(approve)
    expect(mutateWorkflowRun).not.toHaveBeenCalled()
  })
})
