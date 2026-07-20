// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { WorkflowDefinition, WorkflowDetail, WorkflowRunSnapshot } from '@/types/hermes'

import { WorkflowCatalog } from './catalog'
import { $workflowSelectedRunId } from './store'

const getWorkflowRun = vi.fn()
const getWorkflowEvidence = vi.fn()
const listWorkflowAttention = vi.fn()
const listWorkflowEvents = vi.fn()
const listWorkflowRuns = vi.fn()
const listWorkflowDefinitions = vi.fn()
const preflightWorkflow = vi.fn()
const mutateWorkflowRun = vi.fn()
const previewWorkflowCleanup = vi.fn()
const executeWorkflowCleanup = vi.fn()
const apiRequestState = vi.hoisted(() => ({ profile: 'default' as string | null }))
const profileRouting = vi.hoisted(() => ({ ensureGatewayProfile: vi.fn() }))

vi.mock('@/hermes', () => ({
  getApiRequestProfile: () => apiRequestState.profile,
  getWorkflowEvidence: (...args: unknown[]) => getWorkflowEvidence(...args),
  getWorkflowRun: (...args: unknown[]) => getWorkflowRun(...args),
  listWorkflowAttention: (...args: unknown[]) => listWorkflowAttention(...args),
  listWorkflowEvents: (...args: unknown[]) => listWorkflowEvents(...args),
  listWorkflowRuns: (...args: unknown[]) => listWorkflowRuns(...args),
  mutateWorkflowRun: (...args: unknown[]) => mutateWorkflowRun(...args),
  previewWorkflowCleanup: (...args: unknown[]) => previewWorkflowCleanup(...args),
  executeWorkflowCleanup: (...args: unknown[]) => executeWorkflowCleanup(...args)
}))

vi.mock('@/lib/hermes-api', () => ({
  listWorkflowDefinitions: (...args: unknown[]) => listWorkflowDefinitions(...args),
  preflightWorkflow: (...args: unknown[]) => preflightWorkflow(...args),
  WorkflowApiError: class WorkflowApiError extends Error {}
}))

vi.mock('@/store/profile', () => ({ ensureGatewayProfile: profileRouting.ensureGatewayProfile }))

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

function definition(overrides: Partial<WorkflowDefinition> = {}): WorkflowDefinition {
  return {
    description: 'Checks a laptop and produces a diagnostic report.',
    inputs: [],
    name: 'Laptop diagnostic',
    precedence: 1,
    run_support: { reason: 'supported', supported: true },
    source: 'profile',
    supported_inputs: { reason: 'parameterless', supported: true },
    trust_state: 'trusted',
    version: '1.2.0',
    ...overrides
  }
}

function detail(overrides: Partial<WorkflowDetail> = {}): WorkflowDetail {
  return {
    ...definition(),
    compatibility: { findings: [], level: 'supported', runnable: true },
    coordinator: { healthy: true, reason: 'ready', status: 'healthy' },
    definition: { inputs: {}, name: 'Laptop diagnostic' },
    risk_summary: { execution_environment: 'local', risk_level: 'low' },
    topology: { mermaid: null, omitted: null, text: 'start', warnings: [] },
    ...overrides
  }
}

async function renderView(client: QueryClient, initialTab: 'board' | 'workflows' = 'board') {
  const selectedRunId = $workflowSelectedRunId.get()

  const result = await import('./index').then(({ WorkflowsView }) =>
    render(
      <QueryClientProvider client={client}>
        <WorkflowsView />
      </QueryClientProvider>
    )
  )

  if (initialTab === 'board') {
    const boardTab = await screen.findByRole('tab', { name: 'Active board' })

    if (boardTab.getAttribute('aria-selected') !== 'true') {
      fireEvent.click(boardTab)
      $workflowSelectedRunId.set(selectedRunId)
    }

    await waitFor(() => expect(screen.getByRole('main').getAttribute('aria-busy')).toBe('false'))
  }

  return result
}

function setVisibility(value: 'hidden' | 'visible') {
  Object.defineProperty(document, 'visibilityState', { configurable: true, value })
  document.dispatchEvent(new Event('visibilitychange'))
}

beforeEach(() => {
  apiRequestState.profile = 'default'
  profileRouting.ensureGatewayProfile.mockResolvedValue(undefined)

  for (const mock of [
    getWorkflowEvidence,
    getWorkflowRun,
    listWorkflowAttention,
    listWorkflowEvents,
    listWorkflowRuns,
    listWorkflowDefinitions,
    preflightWorkflow,
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
  listWorkflowDefinitions.mockResolvedValue({ items: [definition()], truncated: false })
  preflightWorkflow.mockRejectedValue(new Error('detail unavailable'))
})

afterEach(() => {
  cleanup()
  profileRouting.ensureGatewayProfile.mockReset()
  $workflowSelectedRunId.set(null)
  vi.clearAllMocks()
})

describe('WorkflowsView', () => {
  it('lists Workflows first and renders the catalog columns, rows, descriptions, and actions', async () => {
    $workflowSelectedRunId.set(null)
    listWorkflowDefinitions.mockResolvedValue({
      items: [
        definition(),
        definition({
          description: 'Deploys an approved release to production.',
          inputs: [
            { name: 'environment', required: true, type: 'string' },
            { name: 'revision', required: true, type: 'string' },
            { name: 'ticket', required: false, type: 'string' }
          ],
          name: 'Release deployment',
          source: 'project',
          supported_inputs: { reason: 'flat_inputs', supported: true },
          version: '3.0.1'
        })
      ],
      truncated: false
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')

    const tabs = await screen.findAllByRole('tab')
    expect(tabs.map(tab => tab.textContent)).toEqual(['Workflows', 'Active board', 'History', 'Archive'])
    expect(tabs[0]?.getAttribute('aria-selected')).toBe('true')
    const table = await screen.findByRole('table', { name: 'Workflow catalog' })
    expect(
      within(table)
        .getAllByRole('columnheader')
        .map(header => header.textContent)
    ).toEqual(['Name', 'Version', 'Description', 'Trust', 'Inputs', 'Source', 'Actions'])
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(2)
    expect(rows[0]?.textContent).toContain('Laptop diagnostic')
    expect(rows[0]?.textContent).toContain('1.2.0')
    expect(rows[0]?.textContent).toContain('trusted')
    expect(rows[0]?.textContent).toContain('Profile')
    expect(within(rows[0]!).getByText('Checks a laptop and produces a diagnostic report.').getAttribute('title')).toBe(
      'Checks a laptop and produces a diagnostic report.'
    )
    expect((within(rows[0]!).getByRole('button', { name: 'View' }) as HTMLButtonElement).disabled).toBe(false)
    expect((within(rows[0]!).getByRole('button', { name: 'Run' }) as HTMLButtonElement).disabled).toBe(false)
    expect(rows[1]?.textContent).toContain('Release deployment')
    expect(rows[1]?.textContent).toContain('Project')
    expect(listWorkflowDefinitions).toHaveBeenCalledTimes(1)
    expect(listWorkflowRuns).not.toHaveBeenCalled()
  })

  it('passes the exact catalog definition to View and Run callbacks', async () => {
    const item = definition()
    const onRunWorkflow = vi.fn()
    const onViewWorkflow = vi.fn()
    listWorkflowDefinitions.mockResolvedValue({ items: [item], truncated: false })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={client}>
        <WorkflowCatalog onRunWorkflow={onRunWorkflow} onViewWorkflow={onViewWorkflow} />
      </QueryClientProvider>
    )

    fireEvent.click(await screen.findByRole('button', { name: 'View' }))
    fireEvent.click(screen.getByRole('button', { name: 'Run' }))
    expect(onViewWorkflow).toHaveBeenCalledWith(item)
    expect(onRunWorkflow).toHaveBeenCalledWith(item)
  })

  it('opens the workflow View dialog from the catalog action', async () => {
    $workflowSelectedRunId.set(null)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')
    fireEvent.click(await screen.findByRole('button', { name: 'View' }))

    expect(await screen.findByRole('dialog', { name: 'View Laptop diagnostic' })).toBeTruthy()
  })

  it('restores catalog focus after View transitions to Review and closes', async () => {
    $workflowSelectedRunId.set(null)
    preflightWorkflow.mockResolvedValue(detail())
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')
    const viewTrigger = await screen.findByRole('button', { name: 'View' })
    viewTrigger.focus()
    fireEvent.click(viewTrigger)
    const viewDialog = await screen.findByRole('dialog', { name: 'View Laptop diagnostic' })
    fireEvent.click(await within(viewDialog).findByRole('button', { name: 'Run' }))
    const reviewDialog = await screen.findByRole('dialog', { name: 'Review & Run Laptop diagnostic' })
    fireEvent.click(within(reviewDialog).getByRole('button', { name: 'Close' }))

    await waitFor(() => expect(document.activeElement).toBe(viewTrigger))
  })

  it('derives semantic input badges and explains unsupported input shapes accessibly', async () => {
    $workflowSelectedRunId.set(null)
    listWorkflowDefinitions.mockResolvedValue({
      items: [
        definition({ name: 'Parameterless' }),
        definition({
          inputs: [
            { name: 'one', required: true, type: 'string' },
            { name: 'two', required: true, type: 'number' },
            { name: 'three', required: false, type: 'boolean' }
          ],
          name: 'Three inputs',
          supported_inputs: { reason: 'flat_inputs', supported: true }
        }),
        definition({
          inputs: [{ name: 'nested', required: true, type: 'object' }],
          name: 'Unsupported inputs',
          supported_inputs: { reason: 'unsupported_input_shape', supported: false }
        })
      ],
      truncated: false
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')

    const table = await screen.findByRole('table', { name: 'Workflow catalog' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(within(rows[0]!).getByText('No inputs')).toBeTruthy()
    expect(within(rows[1]!).getByText('3 inputs')).toBeTruthy()
    expect(within(rows[2]!).getByText('1 input').getAttribute('data-slot')).toBe('badge')
    const disabledRun = within(rows[2]!).getByRole('button', { name: 'Run' })
    expect(document.getElementById(disabledRun.getAttribute('aria-describedby')!)?.textContent).toBe(
      'Run is unavailable because this workflow uses unsupported input fields.'
    )
  })

  it('renders a typed error row for a corrupt catalog entry without actions', async () => {
    $workflowSelectedRunId.set(null)
    listWorkflowDefinitions.mockResolvedValue({
      items: [{ error: 'invalid_definition', name: 'broken-workflow.yaml' }],
      truncated: false
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')

    const row = within(await screen.findByRole('table', { name: 'Workflow catalog' })).getAllByRole('row')[1]!
    expect(row.textContent).toContain('broken-workflow.yaml')
    expect(row.textContent).toContain('Invalid workflow definition')
    expect(within(row).queryByRole('button', { name: 'View' })).toBeNull()
    expect(within(row).queryByRole('button', { name: 'Run' })).toBeNull()
  })

  it('renders the shared empty state with a fork-owned workflow documentation pointer', async () => {
    $workflowSelectedRunId.set(null)
    listWorkflowDefinitions.mockResolvedValue({ items: [], truncated: false })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')

    expect(await screen.findByText('No workflows installed')).toBeTruthy()
    expect(screen.getByRole('link', { name: 'Workflow documentation' }).getAttribute('href')).toBe(
      'https://github.com/cmetech/hermes-agent/blob/base/website/docs/user-guide/features/workflows.md'
    )
  })

  it('keeps catalog rows visible while announcing a truncated partial result', async () => {
    $workflowSelectedRunId.set(null)
    listWorkflowDefinitions.mockResolvedValue({ items: [definition()], truncated: true })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')

    expect(await screen.findByRole('table', { name: 'Workflow catalog' })).toBeTruthy()
    expect(screen.getByText('Laptop diagnostic')).toBeTruthy()
    const partial = screen.getByRole('status', { name: 'Partial workflow catalog' })
    expect(partial.getAttribute('data-slot')).toBe('alert')
    expect(partial.textContent).toContain('Only part of the workflow catalog could be loaded.')
  })

  it('renders the shared error state and retries a failed catalog request', async () => {
    $workflowSelectedRunId.set(null)
    listWorkflowDefinitions
      .mockRejectedValueOnce(new Error('gateway unavailable'))
      .mockResolvedValueOnce({ items: [definition()], truncated: false })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')

    expect((await screen.findByRole('alert')).textContent).toContain('Could not load workflows')
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('table', { name: 'Workflow catalog' })).toBeTruthy()
    expect(listWorkflowDefinitions).toHaveBeenCalledTimes(2)
  })

  it('keeps View enabled and makes the disabled Run explanation the next keyboard stop', async () => {
    $workflowSelectedRunId.set(null)
    listWorkflowDefinitions.mockResolvedValue({
      items: [
        definition({
          inputs: [{ name: 'matrix', required: true, type: 'object' }],
          name: 'Unsupported inputs',
          supported_inputs: { reason: 'unsupported_input_shape', supported: false }
        }),
        definition({
          name: 'Untrusted workflow',
          trust_state: 'untrusted'
        })
      ],
      truncated: false
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')

    const rows = within(await screen.findByRole('table', { name: 'Workflow catalog' })).getAllByRole('row')
    const view = within(rows[1]!).getByRole('button', { name: 'View' }) as HTMLButtonElement
    const runButton = within(rows[1]!).getByRole('button', { name: 'Run' }) as HTMLButtonElement
    const runExplanation = within(rows[1]!).getByRole('note', { name: 'Run unavailable' }) as HTMLElement
    expect(view.disabled).toBe(false)
    expect(runButton.disabled).toBe(true)
    expect(runExplanation.getAttribute('aria-disabled')).toBe('true')
    const reasonId = runButton.getAttribute('aria-describedby')
    expect(reasonId).toBeTruthy()
    expect(document.getElementById(reasonId!)?.textContent).toBe(
      'Run is unavailable because this workflow uses unsupported input fields.'
    )

    const keyboardStops = Array.from(
      rows[1]!.querySelectorAll<HTMLElement>(
        'a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])'
      )
    )

    expect(keyboardStops).toEqual([view, runExplanation])
    keyboardStops[0]!.focus()
    expect(document.activeElement).toBe(view)
    keyboardStops[1]!.focus()
    expect(document.activeElement).toBe(runExplanation)
    expect((await screen.findByRole('tooltip')).textContent).toContain(
      'Run is unavailable because this workflow uses unsupported input fields.'
    )
    const untrustedView = within(rows[2]!).getByRole('button', { name: 'View' }) as HTMLButtonElement
    const untrustedRun = within(rows[2]!).getByRole('button', { name: 'Run' }) as HTMLButtonElement
    expect(untrustedView.disabled).toBe(false)
    expect(untrustedRun.disabled).toBe(true)
    expect(document.getElementById(untrustedRun.getAttribute('aria-describedby')!)?.textContent).toBe(
      'Run is unavailable because this workflow failed trust verification.'
    )
  })

  it('binds each catalog request and cache entry to the profile that created it', async () => {
    $workflowSelectedRunId.set(null)
    const profileA = deferred<{ items: WorkflowDefinition[]; truncated: boolean }>()
    const profileB = deferred<{ items: WorkflowDefinition[]; truncated: boolean }>()
    listWorkflowDefinitions.mockImplementation((profile: string | null) => {
      if (profile === 'profile-a') {
        return profileA.promise
      }

      if (profile === 'profile-b') {
        return profileB.promise
      }

      throw new Error(`unexpected profile: ${String(profile)}`)
    })
    apiRequestState.profile = 'profile-a'
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const rendered = await renderView(client, 'workflows')

    expect(listWorkflowDefinitions).toHaveBeenCalledWith('profile-a')
    apiRequestState.profile = 'profile-b'
    rendered.rerender(
      <QueryClientProvider client={client}>
        {await import('./index').then(({ WorkflowsView }) => <WorkflowsView />)}
      </QueryClientProvider>
    )
    await waitFor(() => expect(listWorkflowDefinitions).toHaveBeenCalledWith('profile-b'))

    profileB.resolve({ items: [definition({ name: 'Profile B workflow' })], truncated: false })
    expect(await screen.findByText('Profile B workflow')).toBeTruthy()
    const profileAResult = { items: [definition({ name: 'Profile A workflow' })], truncated: false }
    profileA.resolve(profileAResult)
    await waitFor(() => expect(client.getQueryData(['workflow-catalog', 'profile-a'])).toEqual(profileAResult))
    expect(screen.queryByText('Profile A workflow')).toBeNull()

    apiRequestState.profile = 'profile-a'
    rendered.rerender(
      <QueryClientProvider client={client}>
        {await import('./index').then(({ WorkflowsView }) => <WorkflowsView />)}
      </QueryClientProvider>
    )
    expect(await screen.findByText('Profile A workflow')).toBeTruthy()
    expect(screen.queryByText('Profile B workflow')).toBeNull()
  })

  it('renders localized catalog copy without leaking i18n keys', async () => {
    $workflowSelectedRunId.set(null)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    const { container } = await renderView(client, 'workflows')

    await screen.findByRole('table', { name: 'Workflow catalog' })
    expect(container.textContent).not.toMatch(/operations\.(?:catalog|workflowCatalog)/)
  })

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
    listWorkflowEvents.mockRejectedValue(
      Object.assign(new Error(`${statusCode}: timeline unavailable`), { statusCode })
    )

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
