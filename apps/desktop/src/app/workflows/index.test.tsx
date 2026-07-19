// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { WorkflowRunSnapshot } from '@/types/hermes'

import { $workflowSelectedRunId } from './store'

const getWorkflowRun = vi.fn()
const getWorkflowEvidence = vi.fn()
const listWorkflowAttention = vi.fn()
const listWorkflowEvents = vi.fn()
const listWorkflowRuns = vi.fn()
const mutateWorkflowRun = vi.fn()
const previewWorkflowCleanup = vi.fn()
const executeWorkflowCleanup = vi.fn()

vi.mock('@/hermes', () => ({
  getApiRequestProfile: () => 'default',
  getWorkflowEvidence: (...args: unknown[]) => getWorkflowEvidence(...args),
  getWorkflowRun: (...args: unknown[]) => getWorkflowRun(...args),
  listWorkflowAttention: (...args: unknown[]) => listWorkflowAttention(...args),
  listWorkflowEvents: (...args: unknown[]) => listWorkflowEvents(...args),
  listWorkflowRuns: (...args: unknown[]) => listWorkflowRuns(...args),
  mutateWorkflowRun: (...args: unknown[]) => mutateWorkflowRun(...args),
  previewWorkflowCleanup: (...args: unknown[]) => previewWorkflowCleanup(...args),
  executeWorkflowCleanup: (...args: unknown[]) => executeWorkflowCleanup(...args)
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
  return import('./index').then(({ WorkflowsView }) =>
    render(
      <QueryClientProvider client={client}>
        <WorkflowsView />
      </QueryClientProvider>
    )
  )
}

function setVisibility(value: 'hidden' | 'visible') {
  Object.defineProperty(document, 'visibilityState', { configurable: true, value })
  document.dispatchEvent(new Event('visibilitychange'))
}

beforeEach(() => {
  for (const mock of [
    getWorkflowEvidence,
    getWorkflowRun,
    listWorkflowAttention,
    listWorkflowEvents,
    listWorkflowRuns,
    mutateWorkflowRun,
    previewWorkflowCleanup,
    executeWorkflowCleanup
  ]) {
    mock.mockReset()
  }

  Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
  $workflowSelectedRunId.set('run-1')
  getWorkflowRun.mockResolvedValue(run())
  getWorkflowEvidence.mockResolvedValue({
    items: [],
    kind: 'attempts',
    next_cursor: 0,
    schema_version: 1,
    truncated: false
  })
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
  it('rejects the catalog navigation view before querying the run-list endpoint', async () => {
    const module = await import('./index')

    expect(typeof module.loadWorkflowRunPage).toBe('function')
    await expect(module.loadWorkflowRunPage('workflows')).rejects.toThrow(/does not list workflow runs/i)
    expect(listWorkflowRuns).not.toHaveBeenCalled()
  })

  it.each(['board', 'history', 'archive'] as const)(
    'keeps the %s navigation view on the run-list query seam',
    async view => {
      const module = await import('./index')

      await module.loadWorkflowRunPage(view, 'next-page')

      expect(listWorkflowRuns).toHaveBeenCalledWith('next-page', view)
    }
  )

  it('refetches a stale run after 409 and disables repeat actions until recovery', async () => {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })
    const mutation = deferred<WorkflowRunSnapshot>()
    const refreshed = deferred<WorkflowRunSnapshot>()
    getWorkflowRun.mockResolvedValueOnce(run()).mockImplementationOnce(() => refreshed.promise)
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
    setVisibility('visible')
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    listWorkflowEvents
      .mockResolvedValueOnce({
        cursor_reset: false,
        events: [{ sequence: 1 }, { sequence: 2 }],
        next_cursor: 2,
        schema_version: 1
      })
      .mockResolvedValueOnce({ cursor_reset: true, events: [{ sequence: 10 }], next_cursor: 10, schema_version: 1 })
    await renderView(client)
    fireEvent.mouseDown(await screen.findByRole('tab', { name: 'Timeline events' }), { button: 0, ctrlKey: false })
    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(2))

    await client.refetchQueries({ queryKey: ['workflow-events', 'default', 'run-1'] })

    await waitFor(() => expect(screen.getAllByRole('listitem')).toHaveLength(1))
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

  it('loads selected evidence on demand and sends bounded input through one mutation', async () => {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })
    getWorkflowRun.mockResolvedValue(run({ next_actions: ['provide-input'] }))
    getWorkflowEvidence.mockResolvedValue({
      items: [{ attempt_id: 'attempt-1', state: 'failed' }],
      kind: 'attempts',
      next_cursor: 1,
      schema_version: 1,
      truncated: false
    })
    mutateWorkflowRun.mockResolvedValue(run({ next_actions: ['cancel'], state_version: 2 }))
    await renderView(client)

    fireEvent.mouseDown(await screen.findByRole('tab', { name: 'Attempts' }), { button: 0, ctrlKey: false })
    await waitFor(() => expect(getWorkflowEvidence).toHaveBeenCalledWith('run-1', 'attempts'))
    const attempt = await screen.findByRole('listitem')
    expect(attempt.textContent).toContain('attempt-1')

    fireEvent.change(screen.getByLabelText('Input value'), { target: { value: 'bounded answer' } })
    fireEvent.click(screen.getByRole('button', { name: 'Provide input' }))

    await waitFor(() =>
      expect(mutateWorkflowRun).toHaveBeenCalledWith(
        'run-1',
        'provide-input',
        expect.objectContaining({ expected_version: 1, interaction_id: 'interaction-1', value: 'bounded answer' })
      )
    )
  })

  it('separates archive views from explicit preview-token cleanup', async () => {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })
    previewWorkflowCleanup.mockResolvedValue({
      blocked_reasons: [],
      bytes: 42,
      candidates: [
        { blocked_reasons: [], bytes: 42, evidence_types: ['events'], files: 2, run_id: 'run-1', status: 'succeeded' }
      ],
      confirmation_expires_at: '2026-07-18T01:00:00Z',
      confirmation_token: 'exact-token',
      execute: false,
      files: 2,
      run_ids: ['run-1']
    })
    executeWorkflowCleanup.mockResolvedValue({ bytes: 42, execute: true, files: 2, run_ids: ['run-1'] })
    await renderView(client)

    fireEvent.click(await screen.findByRole('tab', { name: 'Archive' }))
    await waitFor(() => expect(listWorkflowRuns).toHaveBeenCalledWith(undefined, 'archive'))
    fireEvent.click(await screen.findByRole('button', { name: 'Inspect cleanup impact' }))
    await screen.findByText('1 retained runs and 2 evidence files would be quarantined.')
    fireEvent.click(screen.getByRole('button', { name: 'Execute explicit cleanup' }))

    await waitFor(() => expect(executeWorkflowCleanup).toHaveBeenCalledWith('exact-token', '7d'))
  })

  it.each([429, 500])('keeps authoritative actions available when timeline loading fails with %s', async statusCode => {
    setVisibility('visible')
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })
    getWorkflowRun.mockResolvedValue(run({ next_actions: ['approve', 'cancel'] }))
    listWorkflowEvents.mockRejectedValue(Object.assign(new Error(`${statusCode}: timeline unavailable`), { statusCode }))

    await renderView(client)
    await waitFor(() => expect(listWorkflowEvents).toHaveBeenCalled())

    expect((screen.getByRole('button', { name: 'Approve' }) as HTMLButtonElement).disabled).toBe(false)
    expect((screen.getByRole('button', { name: 'Cancel' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('renders actionable attention rows with origin, age, cause, and click-through at laptop width', async () => {
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1024 })
    $workflowSelectedRunId.set(null)
    listWorkflowAttention.mockResolvedValue({
      items: [
        ['approval', 'Approval workflow', 'workflow_approval', 'api', 'approval required', 'approve'],
        ['input', 'Input workflow', 'loop_input', 'chat', 'operator input required', 'provide-input'],
        ['stalled', 'Stalled workflow', 'stalled', 'cron', 'node lease expired', 'resume'],
        ['failure', 'Failed workflow', 'failure', 'desktop', 'command failed', 'retry'],
        ['reconcile', 'Reconcile workflow', 'reconcile', 'background_agent', 'outcome uncertain', 'reconcile']
      ].map(([run_id, workflow, kind, origin, cause, action]) => ({
        cause,
        health: kind === 'stalled' ? 'stalled' : 'user_wait',
        kind,
        next_actions: [action],
        node_id: 'node-1',
        origin,
        run_id,
        state_version: 3,
        status: kind === 'failure' ? 'failed' : 'paused',
        updated_at: new Date(Date.now() - 60_000).toISOString(),
        workflow
      })),
      next_cursor: null,
      schema_version: 1
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client)

    for (const workflow of [
      'Approval workflow',
      'Input workflow',
      'Stalled workflow',
      'Failed workflow',
      'Reconcile workflow'
    ]) {
      expect(await screen.findByText(workflow)).toBeTruthy()
    }

    expect(screen.getByText('approval required')).toBeTruthy()
    expect(screen.getByText('outcome uncertain')).toBeTruthy()
    expect(screen.getAllByText(/1 minute ago/i)).toHaveLength(5)

    for (const summary of [
      'workflow approval · Approve',
      'loop input · Provide input',
      'stalled · Resume',
      'failure · Retry',
      'reconcile · Reconcile'
    ]) {
      expect(screen.getByText(summary)).toBeTruthy()
    }

    fireEvent.click(screen.getByRole('button', { name: 'Open run Approval workflow' }))
    await waitFor(() => expect(getWorkflowRun).toHaveBeenCalledWith('approval'))
  })

  it('marks the workflow region busy during an authoritative refresh', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    await renderView(client)
    const main = await screen.findByRole('main')
    expect(main.getAttribute('aria-busy')).toBe('false')
    const refresh = deferred<{ next_cursor: null; runs: WorkflowRunSnapshot[]; schema_version: number }>()
    listWorkflowRuns.mockImplementationOnce(() => refresh.promise)

    const refetch = client.refetchQueries({ queryKey: ['workflow-runs', 'default', 'board'] })
    await waitFor(() => expect(main.getAttribute('aria-busy')).toBe('true'))
    refresh.resolve({ next_cursor: null, runs: [run()], schema_version: 1 })
    await refetch
    await waitFor(() => expect(main.getAttribute('aria-busy')).toBe('false'))
  })

  it('does not acquire an events poll while the inspector is hidden', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    setVisibility('hidden')

    await renderView(client)
    await waitFor(() => expect(getWorkflowRun).toHaveBeenCalledWith('run-1'))

    expect(listWorkflowEvents).not.toHaveBeenCalled()
  })
})
