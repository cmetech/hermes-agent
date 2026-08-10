// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { I18nProvider, TRANSLATIONS } from '@/i18n'
import type { WorkflowDefinition, WorkflowDetail, WorkflowRunSnapshot } from '@/types/hermes'

import { WorkflowCatalog } from './catalog'
import { isWorkflowAttemptEvidence, isWorkflowPersistentSessionRecoveryEvidence, RunInspector } from './run-inspector'
import { $workflowSelectedRunId } from './store'

const getWorkflowRun = vi.fn()
const getWorkflowEvidence = vi.fn()
const getWorkflowArtifactPreview = vi.fn()
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
const workflowCopy = TRANSLATIONS.en.operations

vi.mock('@/hermes', () => ({
  cancelWorkflowArtifactDownload: vi.fn().mockResolvedValue({ cancelled: true }),
  downloadWorkflowArtifact: vi.fn().mockResolvedValue({ status: 'cancelled' }),
  getApiRequestProfile: () => apiRequestState.profile,
  getWorkflowArtifactPreview: (...args: unknown[]) => getWorkflowArtifactPreview(...args),
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
    definition_digest: 'a'.repeat(64),
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
    compatibility: { level: 'supported', runnable: true },
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

function catalogWithoutCompatibility(representation: 'absent' | 'null' | 'undefined'): WorkflowDefinition {
  const item = definition() as unknown as Record<string, unknown>

  if (representation === 'absent') {
    Reflect.deleteProperty(item, 'compatibility')
  } else {
    item.compatibility = representation === 'null' ? null : undefined
  }

  return item as unknown as WorkflowDefinition
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
    getWorkflowArtifactPreview,
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
  getWorkflowArtifactPreview.mockResolvedValue({
    bytes_returned: 0,
    content: '',
    media_type: 'text/markdown; charset=utf-8',
    publication_id: 'publication-1',
    size_bytes: 0,
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
  it.each([
    ['board', 'Active board'],
    ['history', 'History'],
    ['archive', 'Archive']
  ] as const)('renders the %s view with collapsible lanes and disabled future controls', async (view, label) => {
    $workflowSelectedRunId.set(null)
    listWorkflowRuns.mockResolvedValue({ next_cursor: null, runs: [run()], schema_version: 1 })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')
    fireEvent.click(screen.getByRole('tab', { name: label }))

    expect(await screen.findByLabelText('1 loaded workflow run')).toBeTruthy()
    expect(
      screen.getByLabelText('Workflows activity board').querySelector('[data-layout="collapsible-lanes"]')
    ).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Run filters coming soon' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('textbox', { name: 'Search runs — coming soon' }) as HTMLInputElement).disabled).toBe(true)
    expect(listWorkflowRuns).toHaveBeenCalledWith(undefined, view)

    const runView = screen.getByRole('main').querySelector('[data-workflow-run-view]')!
    expect(runView.className).toContain('min-h-0')
    expect(runView.className).toContain('flex-1')
    expect(runView.className).toContain('overflow-hidden')
    const boardShell = runView.querySelector('[data-workflow-board-shell]')!
    expect(boardShell.className).toContain('min-h-0')
    expect(boardShell.className).toContain('flex-1')
    expect(boardShell.firstElementChild?.className).toContain('h-full')
    expect(boardShell.querySelector('[data-layout="collapsible-lanes"]')?.className).toContain('flex-1')
    expect(boardShell.querySelector('[data-lane-scroll]')?.className).toContain('overflow-y-auto')
  })

  it('keeps the catalog free of run-only toolbar controls', async () => {
    $workflowSelectedRunId.set(null)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')

    expect(screen.queryByRole('textbox', { name: 'Search runs — coming soon' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Run filters coming soon' })).toBeNull()
  })

  it('keeps navigation mounted around a bounded initial run-list loader', async () => {
    const pending = deferred<Awaited<ReturnType<typeof listWorkflowRuns>>>()

    $workflowSelectedRunId.set(null)
    listWorkflowRuns.mockReturnValue(pending.promise)
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')
    const history = screen.getByRole('tab', { name: 'History' })

    history.focus()
    fireEvent.click(history)

    expect(screen.getByRole('status', { name: 'Loading' })).toBeTruthy()
    expect(screen.queryByLabelText('Workflows activity board')).toBeNull()
    expect(globalThis.document.activeElement).toBe(history)

    pending.resolve({ next_cursor: null, runs: [run()], schema_version: 1 })
    expect(await screen.findByLabelText('Workflows activity board')).toBeTruthy()
  })

  it('opens selected run detail in a side drawer instead of below the board', async () => {
    $workflowSelectedRunId.set(null)
    getWorkflowRun.mockResolvedValue(run())
    listWorkflowRuns.mockResolvedValue({ next_cursor: null, runs: [run()], schema_version: 1 })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client)
    const card = await screen.findByRole('button', { name: /Laptop diagnostic/ })
    fireEvent.click(card)

    const drawer = await screen.findByRole('complementary', { name: 'Laptop diagnostic run details' })
    expect(drawer.className).toContain('absolute')
    expect(drawer.closest('main')).toBeTruthy()
    expect(within(drawer).getByRole('complementary', { name: 'Laptop diagnostic run inspector' })).toBeTruthy()
    expect(screen.getAllByRole('complementary')).toHaveLength(2)
    expect(card.getAttribute('aria-expanded')).toBe('true')
    expect(within(drawer).getByRole('tab', { name: 'Overview' })).toBeTruthy()
    expect(within(drawer).getByRole('tab', { name: 'Timeline events' })).toBeTruthy()
    expect(within(drawer).getByRole('tab', { name: 'Attempts' })).toBeTruthy()
    expect(within(drawer).getByRole('tab', { name: 'Logs' })).toBeTruthy()
    expect(within(drawer).getByRole('tab', { name: 'Outputs' })).toBeTruthy()
    expect(within(drawer).getByRole('tab', { name: 'Verified artifacts' })).toBeTruthy()
    expect(within(drawer).getByRole('tab', { name: 'Recovery' })).toBeTruthy()
  })

  it('closes the drawer, clears selected polling, and restores card focus', async () => {
    $workflowSelectedRunId.set(null)
    getWorkflowRun.mockResolvedValue(run())
    listWorkflowRuns.mockResolvedValue({ next_cursor: null, runs: [run()], schema_version: 1 })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client)
    const card = await screen.findByRole('button', { name: /Laptop diagnostic/ })
    fireEvent.click(card)
    fireEvent.click(await screen.findByRole('button', { name: 'Close' }))

    await waitFor(() => expect($workflowSelectedRunId.get()).toBeNull())
    await waitFor(() => expect(globalThis.document.activeElement).toBe(card))
    expect(screen.queryByRole('complementary')).toBeNull()
  })

  it('replaces a stale card origin when Attention opens another run', async () => {
    const runTwo = run({ run_id: 'run-2', workflow: 'Second workflow' })
    $workflowSelectedRunId.set(null)
    listWorkflowRuns.mockResolvedValue({ next_cursor: null, runs: [run(), runTwo], schema_version: 1 })
    listWorkflowAttention.mockResolvedValue({
      items: [
        {
          cause: 'Approval required',
          health: 'user_wait',
          kind: 'approval',
          next_actions: ['approve'],
          node_id: 'approval-1',
          origin: 'workflow',
          run_id: 'run-2',
          state_version: 1,
          status: 'paused',
          updated_at: '2026-08-09T00:00:00Z',
          workflow: 'Second workflow'
        }
      ],
      next_cursor: null,
      schema_version: 1
    })
    getWorkflowRun.mockImplementation((id: string) => Promise.resolve(id === 'run-2' ? runTwo : run()))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client)
    const firstCard = await screen.findByRole('button', { name: /Laptop diagnostic/ })
    fireEvent.click(firstCard)

    const attentionOrigin = screen.getByRole('button', { name: 'Open run Second workflow' })
    fireEvent.click(attentionOrigin)
    fireEvent.click(await screen.findByRole('button', { name: 'Close' }))

    await waitFor(() => expect(globalThis.document.activeElement).toBe(attentionOrigin))
    expect(globalThis.document.activeElement).not.toBe(firstCard)
  })

  it('keeps a selected-run failure inside a closeable drawer', async () => {
    $workflowSelectedRunId.set(null)
    getWorkflowRun.mockRejectedValue(new Error('detail failed'))
    listWorkflowRuns.mockResolvedValue({ next_cursor: null, runs: [run()], schema_version: 1 })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client)
    fireEvent.click(await screen.findByRole('button', { name: /Laptop diagnostic/ }))

    const drawer = await screen.findByRole('complementary')
    expect(await within(drawer).findByText('Could not load run details')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Laptop diagnostic/ })).toBeTruthy()
    fireEvent.click(within(drawer).getByRole('button', { name: 'Close' }))
    expect(screen.queryByRole('complementary')).toBeNull()
  })

  it('closes selected detail without stealing focus when navigation changes', async () => {
    getWorkflowRun.mockResolvedValue(run())
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client)
    expect(await screen.findByRole('complementary', { name: 'Laptop diagnostic run details' })).toBeTruthy()
    const history = screen.getByRole('tab', { name: 'History' })
    history.focus()
    fireEvent.click(history)

    expect($workflowSelectedRunId.get()).toBeNull()
    expect(screen.queryByRole('complementary')).toBeNull()
    expect(globalThis.document.activeElement).toBe(history)
  })

  it('resets the inspector to Overview when another run is selected', async () => {
    const runTwo = run({ run_id: 'run-2', workflow: 'Second workflow' })
    $workflowSelectedRunId.set(null)
    listWorkflowRuns.mockResolvedValue({ next_cursor: null, runs: [run(), runTwo], schema_version: 1 })
    getWorkflowRun.mockImplementation((id: string) => Promise.resolve(id === 'run-2' ? runTwo : run()))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client)
    fireEvent.click(await screen.findByRole('button', { name: /Laptop diagnostic/ }))
    fireEvent.mouseDown(await screen.findByRole('tab', { name: 'Logs' }), { button: 0, ctrlKey: false })
    fireEvent.click(screen.getByRole('button', { name: /Second workflow/ }))

    await waitFor(() => expect(screen.getByRole('tab', { name: 'Overview' }).getAttribute('data-state')).toBe('active'))
  })

  it('does not open a drawer when a background run-list refetch changes cards', async () => {
    $workflowSelectedRunId.set(null)
    listWorkflowRuns.mockResolvedValue({ next_cursor: null, runs: [run()], schema_version: 1 })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client)
    await screen.findByRole('button', { name: /Laptop diagnostic/ })
    listWorkflowRuns.mockResolvedValue({
      next_cursor: null,
      runs: [run(), run({ run_id: 'run-2', workflow: 'Background arrival' })],
      schema_version: 1
    })
    await client.refetchQueries({ queryKey: ['workflow-runs', 'default', 'board'] })

    expect($workflowSelectedRunId.get()).toBeNull()
    expect(screen.queryByRole('complementary')).toBeNull()
  })

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

  it('renders the backend Phase 5 capability summary without resolving a model', async () => {
    listWorkflowDefinitions.mockResolvedValue({
      items: [
        definition({
          language: {
            declared_profile: 'archon-2026-07',
            effective_profile: 'archon-2026-07',
            legacy: false,
            normalizer_version: 5
          },
          provider_capability: {
            authority_digest: 'a'.repeat(64),
            degraded_count: 0,
            level: 'portable',
            mixed_provider: false,
            resolved_route_count: 1,
            schema_version: 1,
            unsupported_count: 0,
            warning_codes: []
          }
        })
      ],
      truncated: false
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')
    const row = within(await screen.findByRole('table', { name: 'Workflow catalog' })).getAllByRole('row')[1]!

    expect(row.textContent).toContain('Provider readiness: portable')
    expect((within(row).getByRole('button', { name: 'Run' }) as HTMLButtonElement).disabled).toBe(false)
    expect(row.textContent).not.toContain('openai/')
  })

  it('shows backend language badges while preserving old-backend source rows', async () => {
    $workflowSelectedRunId.set(null)
    listWorkflowDefinitions.mockResolvedValue({
      items: [
        definition({
          language: { effective_profile: 'archon-2026-07', legacy: false },
          name: 'Archon workflow',
          source: 'project'
        }),
        definition({
          language: { effective_profile: 'hermes-legacy', legacy: true },
          name: 'Legacy workflow'
        }),
        definition({ name: 'Older backend workflow' })
      ],
      truncated: false
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')

    const rows = within(await screen.findByRole('table', { name: 'Workflow catalog' }))
      .getAllByRole('row')
      .slice(1)

    expect(within(rows[0]!).getByText('Archon 2026-07')).toBeTruthy()
    expect(within(rows[1]!).getByText('Legacy semantics')).toBeTruthy()
    expect(within(rows[2]!).getByText('Profile')).toBeTruthy()
    expect(within(rows[2]!).queryByText('Archon 2026-07')).toBeNull()
    expect(within(rows[2]!).queryByText('Legacy semantics')).toBeNull()
  })

  it('renders an unknown future workflow language profile without relabeling it as Archon', async () => {
    $workflowSelectedRunId.set(null)
    listWorkflowDefinitions.mockResolvedValue({
      items: [
        definition({
          language: { effective_profile: 'future-workflow-language' as never, legacy: false }
        })
      ],
      truncated: false
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')

    const row = within(await screen.findByRole('table', { name: 'Workflow catalog' })).getAllByRole('row')[1]!
    expect(within(row).getByText('future-workflow-language')).toBeTruthy()
    expect(within(row).queryByText('Archon 2026-07')).toBeNull()
  })

  it('preserves a whitespace-padded markup-shaped future profile as inert server text', async () => {
    const futureProfile = '  <strong>future-workflow-language</strong>  '
    $workflowSelectedRunId.set(null)
    listWorkflowDefinitions.mockResolvedValue({
      items: [
        definition({
          language: { effective_profile: futureProfile as never, legacy: false }
        })
      ],
      truncated: false
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')

    const row = within(await screen.findByRole('table', { name: 'Workflow catalog' })).getAllByRole('row')[1]!

    const languageBadge = Array.from(row.querySelectorAll('[data-slot="badge"]')).find(badge =>
      badge.textContent?.includes('future-workflow-language')
    )

    expect(languageBadge?.textContent).toBe(futureProfile)
    expect(languageBadge?.querySelector('strong')).toBeNull()
  })

  it('renders generic localized language copy when the backend profile has no safe label', async () => {
    $workflowSelectedRunId.set(null)
    listWorkflowDefinitions.mockResolvedValue({
      items: [definition({ language: { effective_profile: '' as never, legacy: false } })],
      truncated: false
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')

    const row = within(await screen.findByRole('table', { name: 'Workflow catalog' })).getAllByRole('row')[1]!
    expect(within(row).getByText('Workflow language')).toBeTruthy()
    expect(within(row).queryByText('Archon 2026-07')).toBeNull()
  })

  it('shows authenticated AI metadata as information without changing Run policy', async () => {
    $workflowSelectedRunId.set(null)
    listWorkflowDefinitions.mockResolvedValue({
      items: [
        definition({ name: 'AI supported', requires_ai: true }),
        definition({ name: 'Non-AI supported', requires_ai: false }),
        definition({
          compatibility: { level: 'unsupported', runnable: false },
          name: 'AI unsupported inputs',
          requires_ai: true,
          run_support: { reason: 'unsupported_inputs', supported: false },
          supported_inputs: { reason: 'unsupported_input_shape', supported: false },
          trust_state: 'untrusted'
        }),
        definition({
          compatibility: { level: 'unsupported', runnable: false },
          name: 'AI incompatible runtime',
          requires_ai: true,
          trust_state: 'untrusted'
        }),
        definition({ name: 'AI untrusted', requires_ai: true, trust_state: 'untrusted' }),
        definition({ name: 'Legacy payload' })
      ],
      truncated: false
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')

    const rows = within(await screen.findByRole('table', { name: 'Workflow catalog' }))
      .getAllByRole('row')
      .slice(1)

    const aiCopy = 'Runs AI inference through your configured model provider'

    expect(within(rows[0]!).getByText(aiCopy)).toBeTruthy()
    expect(within(rows[1]!).queryByText(aiCopy)).toBeNull()
    expect(within(rows[5]!).queryByText(aiCopy)).toBeNull()
    expect((within(rows[0]!).getByRole('button', { name: 'Run' }) as HTMLButtonElement).disabled).toBe(false)
    expect((within(rows[1]!).getByRole('button', { name: 'Run' }) as HTMLButtonElement).disabled).toBe(false)

    const expectedDisabledReasons = [
      'Run is unavailable because this workflow uses unsupported input fields.',
      workflowCopy.workflowRunIncompatible,
      'Run is unavailable because this workflow failed trust verification.'
    ]

    for (const [index, expectedReason] of expectedDisabledReasons.entries()) {
      const row = rows[index + 2]!
      expect(within(row).getByText(aiCopy)).toBeTruthy()
      const button = within(row).getByRole('button', { name: 'Run' }) as HTMLButtonElement
      expect(button.disabled).toBe(true)
      expect(document.getElementById(button.getAttribute('aria-describedby')!)?.textContent).toBe(expectedReason)
    }
  })

  it('keeps colliding bundled showcases distinct and presents their authoritative Run support honestly', async () => {
    $workflowSelectedRunId.set(null)
    listWorkflowDefinitions.mockResolvedValue({
      items: [
        definition({ name: 'approval-gate', source: 'project' }),
        definition({
          name: 'approval-gate',
          precedence: 3,
          source: 'showcase',
          trust_state: 'verified_bundled'
        }),
        definition({
          inputs: [
            { max_bytes: 4096, name: 'symptom', required: true, type: 'text' },
            { name: 'evidence', required: true, type: 'file' }
          ],
          name: 'laptop-diagnostic',
          precedence: 3,
          run_support: { reason: 'supported', supported: true },
          source: 'showcase',
          supported_inputs: { reason: 'flat_inputs', supported: true },
          trust_state: 'verified_bundled'
        }),
        definition({
          compatibility: { level: 'unsupported', runnable: false },
          name: 'ai-extensions',
          precedence: 3,
          run_support: { reason: 'showcase_cli_required', supported: false },
          source: 'showcase',
          trust_state: 'verified_bundled'
        }),
        definition({
          name: 'scheduling',
          precedence: 3,
          run_support: { reason: 'schedule_required', supported: false },
          source: 'showcase',
          trust_state: 'verified_bundled'
        })
      ],
      truncated: false
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')

    const rows = within(await screen.findByRole('table', { name: 'Workflow catalog' }))
      .getAllByRole('row')
      .slice(1)

    expect(rows).toHaveLength(5)
    expect(rows[0]?.textContent).toContain('Project')
    expect(rows[1]?.textContent).toContain('Bundled showcase')
    expect(rows[1]?.textContent).toContain('verified bundle')
    expect((within(rows[1]!).getByRole('button', { name: 'Run' }) as HTMLButtonElement).disabled).toBe(false)
    expect(rows[2]?.textContent).toContain('2 inputs')
    expect((within(rows[2]!).getByRole('button', { name: 'Run' }) as HTMLButtonElement).disabled).toBe(false)
    expect(rows[3]?.textContent).toContain('Incompatible')

    for (const row of rows.slice(3)) {
      expect(row.textContent).toContain('Bundled showcase')
      expect(row.textContent).toContain('verified bundle')
      expect((within(row).getByRole('button', { name: 'View' }) as HTMLButtonElement).disabled).toBe(false)
    }

    for (const row of rows.slice(3, 4)) {
      const runButton = within(row).getByRole('button', { name: 'Run' }) as HTMLButtonElement
      expect(runButton.disabled).toBe(true)
      expect(document.getElementById(runButton.getAttribute('aria-describedby')!)?.textContent).toBe(
        'Run this bundled showcase from the CLI.'
      )
    }

    expect((within(rows[4]!).getByRole('button', { name: 'Run' }) as HTMLButtonElement).disabled).toBe(false)
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
          run_support: { reason: 'unsupported_inputs', supported: false },
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

  it('derives unavailable Run copy from support reason and compatibility rather than catalog source', async () => {
    $workflowSelectedRunId.set(null)
    listWorkflowDefinitions.mockResolvedValue({
      items: [
        definition({
          name: 'Profile unsupported inputs',
          run_support: { reason: 'unsupported_inputs', supported: false },
          supported_inputs: { reason: 'unsupported_input_shape', supported: false }
        }),
        definition({
          compatibility: { level: 'unsupported', runnable: false },
          name: 'Profile incompatible runtime'
        })
      ],
      truncated: false
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')

    const rows = within(await screen.findByRole('table', { name: 'Workflow catalog' }))
      .getAllByRole('row')
      .slice(1)

    const unsupportedRun = within(rows[0]!).getByRole('button', { name: 'Run' }) as HTMLButtonElement
    const incompatibleRun = within(rows[1]!).getByRole('button', { name: 'Run' }) as HTMLButtonElement

    expect(unsupportedRun.disabled).toBe(true)
    expect(document.getElementById(unsupportedRun.getAttribute('aria-describedby')!)?.textContent).toBe(
      'Run is unavailable because this workflow uses unsupported input fields.'
    )
    expect(incompatibleRun.disabled).toBe(true)
    expect(document.getElementById(incompatibleRun.getAttribute('aria-describedby')!)?.textContent).toBe(
      workflowCopy.workflowRunIncompatible
    )
  })

  it('fails closed without crashing when an older backend omits catalog run support', async () => {
    $workflowSelectedRunId.set(null)
    listWorkflowDefinitions.mockResolvedValue({
      items: [definition({ run_support: undefined as never })],
      truncated: false
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')

    const row = within(await screen.findByRole('table', { name: 'Workflow catalog' })).getAllByRole('row')[1]!
    expect(within(row).getByRole('button', { name: 'View' })).toBeTruthy()
    const run = within(row).getByRole('button', { name: 'Run' }) as HTMLButtonElement
    expect(run.disabled).toBe(true)
    expect(document.getElementById(run.getAttribute('aria-describedby')!)?.textContent).toBe(
      workflowCopy.workflowRunSupportUnavailable
    )
  })

  it.each(['absent', 'undefined', 'null'] as const)(
    'fails closed without requiring a coordinator when catalog compatibility is %s',
    async representation => {
      $workflowSelectedRunId.set(null)
      listWorkflowDefinitions.mockResolvedValue({
        items: [catalogWithoutCompatibility(representation)],
        truncated: false
      })
      const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

      await renderView(client, 'workflows')

      const row = within(await screen.findByRole('table', { name: 'Workflow catalog' })).getAllByRole('row')[1]!
      const run = within(row).getByRole('button', { name: 'Run' }) as HTMLButtonElement
      expect(run.disabled).toBe(true)
      expect(document.getElementById(run.getAttribute('aria-describedby')!)?.textContent).toBe(
        workflowCopy.workflowRunIncompatible
      )
    }
  )

  it('keeps Run disabled for an unknown backend run-support reason', async () => {
    $workflowSelectedRunId.set(null)
    listWorkflowDefinitions.mockResolvedValue({
      items: [
        definition({
          compatibility: { level: 'unsupported', runnable: false },
          run_support: { reason: 'future_backend_rule' as never, supported: false },
          trust_state: 'trusted'
        })
      ],
      truncated: false
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')

    const row = within(await screen.findByRole('table', { name: 'Workflow catalog' })).getAllByRole('row')[1]!
    const run = within(row).getByRole('button', { name: 'Run' }) as HTMLButtonElement
    expect(run.disabled).toBe(true)
    expect(globalThis.document.getElementById(run.getAttribute('aria-describedby')!)?.textContent).toBe(
      workflowCopy.workflowRunSupportUnavailable
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
          run_support: { reason: 'unsupported_inputs', supported: false },
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

    // Upstream v0.19.0 gated Radix's focus-open to KEYBOARD focus
    // (suppressNonKeyboardFocusOpen in components/ui/tooltip.tsx), so a menu
    // closing no longer leaves a stale tip over its trigger. jsdom does not
    // mark a programmatic .focus() as :focus-visible, so without this the
    // guard cancels a tooltip that a real Tab focus DOES show. Emulate
    // keyboard focus for the focused element only — same technique as
    // upstream's own tooltip.test.tsx, which stubs `matches`.
    const originalMatches = HTMLElement.prototype.matches
    // `Element['matches']` is an overload set whose tag-name overloads are type
    // predicates, which a plain function expression can never satisfy. The stub
    // is behaviourally a boolean predicate, so cast it back onto the slot.
    HTMLElement.prototype.matches = function (this: HTMLElement, selector: string): boolean {
      if (selector === ':focus-visible') {
        return this === document.activeElement
      }

      return originalMatches.call(this, selector)
    } as typeof HTMLElement.prototype.matches

    try {
      keyboardStops[0]!.focus()
      expect(document.activeElement).toBe(view)
      keyboardStops[1]!.focus()
      expect(document.activeElement).toBe(runExplanation)
      expect((await screen.findByRole('tooltip')).textContent).toContain(
        'Run is unavailable because this workflow uses unsupported input fields.'
      )
    } finally {
      HTMLElement.prototype.matches = originalMatches
    }

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

  it('catches signal and dependency copy falling back to English in any locale', () => {
    const stringKeys = [
      'acceptResult',
      'continueWithFeedback',
      'feedback',
      'workflowDependencies',
      'workflowDependencySources',
      'workflowDependencyPrecedence',
      'workflowDependencyPackages',
      'workflowExpandedNodes',
      'workflowExpandedEdges',
      'workflowIncludeDepth',
      'workflowCompositeDigest',
      'workflowIgnoredPolicies',
      'workflowLaneEmpty',
      'workflowRunFiltersComingSoon',
      'workflowRunSearchComingSoon',
      'workflowRunDetailLoading',
      'workflowRunDetailError'
    ] as const

    const english = TRANSLATIONS.en.operations

    expect(english.acceptResult).toBe('Accept result')
    expect(english.continueWithFeedback).toBe('Continue with feedback')
    expect(english.feedback).toBe('Feedback')
    expect(english.workflowIgnoredPolicyField('required secrets')).toBe('Ignored: required secrets')

    for (const locale of ['en', 'ar', 'ja', 'zh', 'zh-hant'] as const) {
      const copy = TRANSLATIONS[locale].operations

      for (const key of stringKeys) {
        expect(copy[key], `${locale}.${key}`).toBeTypeOf('string')
        expect(copy[key].length, `${locale}.${key}`).toBeGreaterThan(0)

        if (locale !== 'en') {
          expect(copy[key], `${locale}.${key} must not use the English fallback`).not.toBe(english[key])
        }
      }

      expect(copy.workflowIgnoredPolicyField, `${locale}.workflowIgnoredPolicyField`).toBeTypeOf('function')
      expect(copy.workflowLaneExpand('Lane')).toBeTypeOf('string')
      expect(copy.workflowLaneCollapse('Lane')).toBeTypeOf('string')
      expect(copy.workflowRunDrawerLabel('Workflow')).toBeTypeOf('string')
      expect(copy.workflowLoadedRunCount(2)).toBeTypeOf('string')

      if (locale !== 'en') {
        expect(copy.workflowIgnoredPolicyField('required secrets')).not.toBe(
          english.workflowIgnoredPolicyField('required secrets')
        )
        expect(copy.workflowLaneExpand('Lane')).not.toBe(english.workflowLaneExpand('Lane'))
        expect(copy.workflowLaneCollapse('Lane')).not.toBe(english.workflowLaneCollapse('Lane'))
        expect(copy.workflowRunDrawerLabel('Workflow')).not.toBe(english.workflowRunDrawerLabel('Workflow'))
        expect(copy.workflowLoadedRunCount(2)).not.toBe(english.workflowLoadedRunCount(2))
      }
    }
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

  it('does not diagnose a run-list decoding failure as a disabled workflow plugin', async () => {
    $workflowSelectedRunId.set(null)
    listWorkflowRuns.mockRejectedValue(new Error('Hermes returned an invalid workflow run page'))
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    await renderView(client, 'workflows')
    fireEvent.click(await screen.findByRole('tab', { name: 'History' }))

    expect(await screen.findByText('Workflow data is unavailable. Check your connection and try again.')).toBeTruthy()
    expect(screen.queryByText(/plugins enable workflow/i)).toBeNull()
  })

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

  it('catches signal confirmations rendered with generic labels or submit-ready empty feedback', () => {
    const onAction = vi.fn()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={client}>
        <RunInspector
          onAction={onAction}
          run={run({
            next_actions: ['approve', 'provide-input', 'cancel'],
            pending_interaction: {
              interaction_id: 'signal-1',
              iteration: 2,
              max_iterations: 3,
              type: 'loop_signal_confirmation'
            }
          })}
        />
      </QueryClientProvider>
    )

    expect((screen.getByRole('button', { name: 'Accept result' }) as HTMLButtonElement).disabled).toBe(false)
    expect((screen.getByRole('button', { name: 'Continue with feedback' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('Feedback'), { target: { value: 'Tighten it' } })
    expect((screen.getByRole('button', { name: 'Continue with feedback' }) as HTMLButtonElement).disabled).toBe(false)

    fireEvent.click(screen.getByRole('button', { name: 'Accept result' }))
    fireEvent.click(screen.getByRole('button', { name: 'Continue with feedback' }))
    expect(onAction).toHaveBeenNthCalledWith(1, 'approve')
    expect(onAction).toHaveBeenNthCalledWith(2, 'provide-input', { value: 'Tighten it' })
  })

  it('catches feedback state being reused across distinct signal confirmations', () => {
    const onAction = vi.fn()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    const inspector = (interactionId: string, iteration: number) => (
      <QueryClientProvider client={client}>
        <RunInspector
          onAction={onAction}
          run={run({
            next_actions: ['approve', 'provide-input', 'cancel'],
            pending_interaction: {
              interaction_id: interactionId,
              iteration,
              max_iterations: 3,
              type: 'loop_signal_confirmation'
            }
          })}
        />
      </QueryClientProvider>
    )

    const view = render(inspector('signal-1', 1))

    fireEvent.change(screen.getByLabelText('Feedback'), { target: { value: 'Tighten it' } })
    fireEvent.click(screen.getByRole('button', { name: 'Continue with feedback' }))
    expect(onAction).toHaveBeenCalledWith('provide-input', { value: 'Tighten it' })

    view.rerender(inspector('signal-2', 2))

    expect((screen.getByLabelText('Feedback') as HTMLInputElement).value).toBe('')
    expect((screen.getByRole('button', { name: 'Continue with feedback' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('catches final signal confirmations inventing a feedback action the backend omitted', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={client}>
        <RunInspector
          onAction={vi.fn()}
          run={run({
            next_actions: ['approve', 'cancel'],
            pending_interaction: {
              interaction_id: 'signal-final',
              iteration: 3,
              max_iterations: 3,
              type: 'loop_signal_confirmation'
            }
          })}
        />
      </QueryClientProvider>
    )

    expect((screen.getByRole('button', { name: 'Accept result' }) as HTMLButtonElement).disabled).toBe(false)
    expect(screen.queryByLabelText('Feedback')).toBeNull()
    expect(screen.queryByRole('button', { name: 'Continue with feedback' })).toBeNull()
    expect((screen.getByRole('button', { name: 'Cancel' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('catches signal labels leaking onto an ordinary loop input', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={client}>
        <RunInspector
          onAction={vi.fn()}
          run={run({
            next_actions: ['provide-input', 'cancel'],
            pending_interaction: { interaction_id: 'input-1', type: 'loop_input' }
          })}
        />
      </QueryClientProvider>
    )

    expect(screen.getByLabelText('Input value')).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Provide input' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.queryByLabelText('Feedback')).toBeNull()
  })

  it('catches an unknown future interaction being relabeled as a signal confirmation', () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={client}>
        <RunInspector
          onAction={vi.fn()}
          run={run({
            next_actions: ['approve', 'provide-input', 'cancel'],
            pending_interaction: {
              interaction_id: 'future-1',
              iteration: 1,
              max_iterations: 2,
              type: 'loop_signal_confirmation_v2' as never
            }
          })}
        />
      </QueryClientProvider>
    )

    expect((screen.getByRole('button', { name: 'Approve' }) as HTMLButtonElement).disabled).toBe(false)
    expect((screen.getByRole('button', { name: 'Provide input' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.queryByRole('button', { name: 'Accept result' })).toBeNull()
    expect(screen.queryByLabelText('Feedback')).toBeNull()
  })

  it('renders the server-derived scheduled wait with localized and canonical instants', async () => {
    const scheduleAt = '2099-01-02T03:04:05Z'
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    getWorkflowRun.mockResolvedValue(
      run({
        blocking_reason: 'scheduled_wait',
        next_actions: ['cancel'],
        presentation_state: 'scheduled_wait',
        schedule_at: scheduleAt,
        status: 'queued'
      })
    )

    await renderView(client)

    expect(await screen.findByText('Scheduled')).toBeTruthy()
    expect(screen.getByText(scheduleAt)).toBeTruthy()
    expect(screen.getByText(new Date(scheduleAt).toLocaleString())).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Cancel' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('formats a scheduled local instant with the active non-English locale', () => {
    const scheduleAt = '2099-01-02T03:04:05Z'
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={client}>
        <I18nProvider configClient={null} initialLocale="ja">
          <RunInspector
            run={run({
              blocking_reason: 'scheduled_wait',
              next_actions: ['cancel'],
              presentation_state: 'scheduled_wait',
              schedule_at: scheduleAt,
              status: 'queued'
            })}
          />
        </I18nProvider>
      </QueryClientProvider>
    )

    expect(screen.getByText(new Date(scheduleAt).toLocaleString('ja'))).toBeTruthy()
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

    const attemptEvidence = {
      attempt_id: 'attempt-1',
      error: { code: 'workflow_operation_failed', message: 'Workflow operation failed.' },
      item_type: 'attempt',
      node_id: 'producer',
      retry: {
        additional_provider_attempts: 0,
        capped: false,
        effective_total_attempts: 3,
        remaining_attempts: 0,
        requested_retries: 2,
        requested_total_attempts: 3,
        retry_consumed: 3
      },
      state: 'failed'
    }

    getWorkflowEvidence.mockResolvedValue({
      items: [attemptEvidence],
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
    expect(attempt.textContent).toBe(
      'producer · attempt-1 · failed · workflow_operation_failed: Workflow operation failed.'
    )

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

  it('requests and renders generic persistent-session recovery evidence', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    getWorkflowEvidence.mockResolvedValue({
      items: [
        {
          attempt_id: 'attempt-1',
          cache_fingerprint_sha256: 'b'.repeat(64),
          item_type: 'recovery',
          missing_session_sha256: 'a'.repeat(64),
          node_id: 'producer',
          outcome: 'stale_entry_replaced',
          provider: 'test-provider',
          provider_attempts_before_recovery: 0,
          recovery_kind: 'persistent_session',
          registry_generation: 7,
          runtime_profile: 'default',
          source: 'cross_run_registry'
        }
      ],
      kind: 'recovery',
      next_cursor: 1,
      schema_version: 1,
      truncated: false
    })

    render(
      <QueryClientProvider client={client}>
        <RunInspector run={run()} />
      </QueryClientProvider>
    )
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Recovery' }), { button: 0, ctrlKey: false })

    await waitFor(() => expect(getWorkflowEvidence).toHaveBeenCalledWith('run-1', 'recovery'))
    expect(await screen.findByText('producer · persistent_session · stale_entry_replaced')).toBeTruthy()
  })

  it('narrows only complete Phase 3 attempt and persistent-session recovery evidence', () => {
    const attempt: Record<string, unknown> = {
      attempt_id: 'attempt-1',
      item_type: 'attempt',
      node_id: 'producer',
      retry: {
        additional_provider_attempts: 2,
        capped: false,
        effective_total_attempts: 3,
        remaining_attempts: 0,
        requested_retries: 2,
        requested_total_attempts: 3,
        retry_consumed: 3
      },
      state: 'succeeded'
    }

    const recovery: Record<string, unknown> = {
      attempt_id: 'attempt-2',
      cache_fingerprint_sha256: 'b'.repeat(64),
      item_type: 'recovery',
      missing_session_sha256: 'a'.repeat(64),
      node_id: 'producer',
      outcome: 'stale_entry_replaced',
      provider: 'test-provider',
      provider_attempts_before_recovery: 0,
      recovery_kind: 'persistent_session',
      registry_generation: 7,
      runtime_profile: 'default',
      source: 'cross_run_registry'
    }

    expect(isWorkflowAttemptEvidence(attempt)).toBe(true)

    if (!isWorkflowAttemptEvidence(attempt)) {
      throw new Error('attempt evidence did not narrow')
    }

    expect(attempt.retry.additional_provider_attempts).toBe(2)
    expect(isWorkflowAttemptEvidence({ attempt_id: 'legacy-attempt', state: 'failed' })).toBe(false)

    expect(isWorkflowPersistentSessionRecoveryEvidence(recovery)).toBe(true)

    if (!isWorkflowPersistentSessionRecoveryEvidence(recovery)) {
      throw new Error('recovery evidence did not narrow')
    }

    expect(recovery.recovery_kind).toBe('persistent_session')
    expect(isWorkflowPersistentSessionRecoveryEvidence({ recovery_kind: 'persistent_session' })).toBe(false)
  })

  it('uses typed artifacts only for backend-confirmed publication identities and preserves legacy evidence fallback', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    getWorkflowEvidence.mockResolvedValueOnce({
      items: [
        {
          integrity_status: 'legacy_unverified',
          item_type: 'artifact',
          publication_id: 'legacy-artifact',
          recovery_status: 'projection_recovered'
        },
        {
          attempt_id: 'attempt-1',
          integrity_status: 'verified',
          item_type: 'artifact',
          media_type: 'text/markdown; charset=utf-8',
          node_id: 'producer',
          output_type: 'Report',
          publication_id: 'publication-1',
          recovery_status: 'verified',
          sha256: 'b'.repeat(64),
          size_bytes: 42
        }
      ],
      kind: 'artifacts',
      next_cursor: 0,
      schema_version: 1,
      truncated: false
    })
    await renderView(client)
    fireEvent.mouseDown(await screen.findByRole('tab', { name: 'Verified artifacts' }), {
      button: 0,
      ctrlKey: false
    })

    expect(await screen.findByText('Report')).toBeTruthy()
    expect(screen.getByRole('button', { name: /Download artifact:/ })).toBeTruthy()
    expect(screen.queryByRole('link', { name: /Download artifact:/ })).toBeNull()
    expect(screen.queryByText(/legacy\.txt/)).toBeNull()

    cleanup()
    getWorkflowEvidence.mockResolvedValueOnce({
      items: [
        {
          integrity_status: 'legacy_unverified',
          item_type: 'artifact',
          publication_id: 'legacy-artifact',
          recovery_status: 'projection_recovered'
        }
      ],
      kind: 'artifacts',
      next_cursor: 0,
      schema_version: 1,
      truncated: false
    })
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <RunInspector run={run()} />
      </QueryClientProvider>
    )
    fireEvent.mouseDown(screen.getByRole('tab', { name: 'Verified artifacts' }), { button: 0, ctrlKey: false })

    expect(await screen.findByText(/legacy-artifact/)).toBeTruthy()
    expect(screen.queryByRole('link', { name: 'Download artifact' })).toBeNull()
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
        ['reconcile', 'Reconcile workflow', 'reconcile', 'background_agent', 'outcome uncertain', 'reconcile'],
        ['signal', 'Signal workflow', 'loop_signal_confirmation', 'desktop', 'review result', 'approve']
      ].map(([run_id, workflow, kind, origin, cause, action]) => ({
        cause,
        health: kind === 'stalled' ? 'stalled' : 'user_wait',
        interaction:
          kind === 'loop_signal_confirmation'
            ? { interaction_id: 'signal-1', iteration: 1, max_iterations: 2, type: kind }
            : undefined,
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
      'Reconcile workflow',
      'Signal workflow'
    ]) {
      expect(await screen.findByText(workflow)).toBeTruthy()
    }

    expect(screen.getByText('approval required')).toBeTruthy()
    expect(screen.getByText('outcome uncertain')).toBeTruthy()
    expect(screen.getAllByText(/1 minute ago/i)).toHaveLength(6)

    for (const summary of [
      'workflow approval · Approve',
      'loop input · Provide input',
      'stalled · Resume',
      'failure · Retry',
      'reconcile · Reconcile',
      'loop signal confirmation · Accept result'
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
