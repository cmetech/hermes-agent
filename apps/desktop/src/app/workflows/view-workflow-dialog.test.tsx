// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { openModalOwnsKeyboard } from '@/app/hooks/use-keybinds'
import { setApiRequestProfile } from '@/hermes'
import type { WorkflowDefinition, WorkflowDetail } from '@/types/hermes'

import { workflowDetailQueryOptions } from './detail-query'

import { WorkflowsView } from './index'

const WORKFLOW_NAME = 'Portable contract'
const MERMAID_SOURCE = 'flowchart TD\n  start["Start"] --> finish["Finish"]'
const renderer = vi.hoisted(() => ({ calls: vi.fn<(code: string) => void>() }))
const profileRouting = vi.hoisted(() => ({ ensureGatewayProfile: vi.fn() }))

vi.mock('@/components/assistant-ui/embeds/mermaid-embed', () => ({
  default: ({ code }: { code: string }) => {
    renderer.calls(code)

    if (code === 'renderer throws') {
      throw new Error('renderer failed')
    }

    return <div data-testid="shared-mermaid-renderer">{code}</div>
  }
}))

vi.mock('@/store/profile', () => ({
  $newChatProfile: { set: vi.fn() },
  cycleProfile: vi.fn(),
  ensureGatewayProfile: profileRouting.ensureGatewayProfile,
  requestProfileCreate: vi.fn(),
  switchProfileToSlot: vi.fn(),
  switchToDefaultProfile: vi.fn(),
  toggleShowAllProfiles: vi.fn()
}))

vi.mock('@/themes/context', () => ({
  useTheme: () => ({ resolvedMode: 'light', setMode: vi.fn() })
}))

interface StructuredRequest {
  body?: Record<string, unknown>
  method?: string
  path: string
  profile?: string
}

function definition(overrides: Partial<WorkflowDefinition> = {}): WorkflowDefinition {
  return {
    description: 'Checks a release before deployment.',
    inputs: [],
    name: WORKFLOW_NAME,
    precedence: 2,
    source: 'profile',
    supported_inputs: { reason: 'parameterless', supported: true },
    trust_state: 'trusted',
    version: '1.0.0',
    ...overrides
  }
}

function detail(overrides: Partial<WorkflowDetail> = {}): WorkflowDetail {
  return {
    ...definition(),
    compatibility: { findings: [], level: 'supported', runnable: true },
    coordinator: { healthy: true, reason: 'ready', status: 'healthy' },
    definition: {
      zeta: 2,
      nested: { zulu: '[REDACTED]', alpha: 1 },
      steps: [{ zulu: 2, alpha: 1 }],
      alpha: 'first'
    },
    risk_summary: { risk_level: 'low' },
    topology: { mermaid: MERMAID_SOURCE, omitted: null, text: 'Start\n  Finish', warnings: [] },
    ...overrides
  }
}

function startResponse() {
  return {
    ok: true,
    value: {
      error: null,
      ok: true,
      result: {
        admission_disposition: 'created',
        blocked_by_run_id: null,
        queue_position: 1,
        run_id: 'run-created',
        status: 'queued'
      },
      schema_version: 1
    }
  }
}

function expectDisabledRunReason(dialog: HTMLElement, expected: string) {
  const run = within(dialog).getByRole('button', { name: 'Run' })
  const reasonId = run.getAttribute('aria-describedby')
  const reason = reasonId ? document.getElementById(reasonId) : null

  expect(run.hasAttribute('disabled')).toBe(true)
  expect(reasonId).toBeTruthy()
  expect(reason?.textContent).toBe(expected)
  expect(reason?.classList.contains('sr-only')).toBe(false)
  expect(dialog.contains(reason)).toBe(true)

  return run
}

describe('workflow View dialog', () => {
  const api = vi.fn()
  const apiStructured = vi.fn()
  const writeClipboard = vi.fn()
  let currentCatalogDefinition: WorkflowDefinition
  let currentDetail: WorkflowDetail
  let detailResponses: Array<unknown>

  function renderView() {
    const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })

    return render(
      <StrictMode>
        <QueryClientProvider client={client}>
          <WorkflowsView />
        </QueryClientProvider>
      </StrictMode>
    )
  }

  async function openView() {
    fireEvent.click(await screen.findByRole('button', { name: 'View' }))

    return screen.findByRole('dialog', { name: `View ${WORKFLOW_NAME}` })
  }

  beforeEach(() => {
    setApiRequestProfile('profile-a')
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
    Object.defineProperties(HTMLElement.prototype, {
      hasPointerCapture: { configurable: true, value: vi.fn(() => false) },
      releasePointerCapture: { configurable: true, value: vi.fn() },
      scrollIntoView: { configurable: true, value: vi.fn() },
      setPointerCapture: { configurable: true, value: vi.fn() }
    })
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: { api, apiStructured, writeClipboard }
    })
    currentCatalogDefinition = definition()
    currentDetail = detail()
    detailResponses = []
    profileRouting.ensureGatewayProfile.mockResolvedValue(undefined)
    api.mockRejectedValue(new Error('unexpected legacy workflow request'))
    apiStructured.mockImplementation(async (request: StructuredRequest) => {
      if (request.path === '/api/plugins/workflow/workflows') {
        return { ok: true, value: { items: [currentCatalogDefinition], truncated: false } }
      }

      if (request.path === `/api/plugins/workflow/workflows/${encodeURIComponent(WORKFLOW_NAME)}`) {
        return detailResponses.shift() ?? { ok: true, value: currentDetail }
      }

      if (request.path === '/api/plugins/workflow/runs') {
        return startResponse()
      }

      throw new Error(`unexpected structured request: ${request.path}`)
    })
  })

  afterEach(() => {
    cleanup()
    setApiRequestProfile(null)
    api.mockReset()
    apiStructured.mockReset()
    writeClipboard.mockReset()
    renderer.calls.mockReset()
    profileRouting.ensureGatewayProfile.mockReset()
    Reflect.deleteProperty(window, 'hermesDesktop')
    vi.restoreAllMocks()
  })

  it('configures workflow detail requests as one-shot until explicit Retry', () => {
    expect(workflowDetailQueryOptions(WORKFLOW_NAME, 'profile-a').retry).toBe(false)
  })

  it('fetches the captured profile and renders topology through the shared Mermaid component', async () => {
    renderView()
    const dialog = await openView()

    expect((await within(dialog).findByTestId('shared-mermaid-renderer')).textContent).toBe(MERMAID_SOURCE)
    expect(renderer.calls).toHaveBeenCalledWith(MERMAID_SOURCE)
    expect(apiStructured).toHaveBeenCalledWith({
      path: `/api/plugins/workflow/workflows/${encodeURIComponent(WORKFLOW_NAME)}`,
      profile: 'profile-a'
    })
    expect(
      apiStructured.mock.calls.filter(
        ([request]) => request.path === `/api/plugins/workflow/workflows/${encodeURIComponent(WORKFLOW_NAME)}`
      )
    ).toHaveLength(1)
    expect(openModalOwnsKeyboard()).toBe(true)
    expect(dialog.querySelector('[data-workflow-view-scroll]')).toBeTruthy()
  })

  it('preserves the shared dialog vertical scroll while clipping horizontal overflow', async () => {
    renderView()
    const dialog = await openView()

    expect(dialog.classList.contains('overflow-y-auto')).toBe(true)
    expect(dialog.classList.contains('overflow-x-hidden')).toBe(true)
    expect(dialog.classList.contains('overflow-hidden')).toBe(false)
  })

  it('shows the bounded outline and omission reason without mounting Mermaid', async () => {
    currentDetail = detail({
      topology: {
        mermaid: null,
        omitted: 'topology_mermaid_too_many_nodes',
        text: 'Start\n  132 bounded nodes',
        warnings: []
      }
    })
    renderView()
    const dialog = await openView()

    expect(
      await within(dialog).findByText('Diagram omitted because the workflow is too large — showing outline.')
    ).toBeTruthy()
    expect(within(dialog).getByText(/132 bounded nodes/)).toBeTruthy()
    expect(renderer.calls).not.toHaveBeenCalled()
  })

  it('keeps the modal interactive and shows source when the shared renderer throws', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined)
    currentDetail = detail({
      topology: { mermaid: 'renderer throws', omitted: null, text: 'safe outline', warnings: [] }
    })
    renderView()
    const dialog = await openView()

    expect(await within(dialog).findByText('renderer throws')).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Definition' }))
    expect((await within(dialog).findByTestId('workflow-definition-json')).textContent).toContain('[REDACTED]')
    expect(within(dialog).getByRole('button', { name: 'Run' })).toBeTruthy()
  })

  it('surfaces a typed fetch error and retries the same profile-bound query', async () => {
    detailResponses = [
      {
        body: { detail: { code: 'coordinator_unavailable', message: 'Coordinator unavailable', retryable: true } },
        ok: false,
        status: 503
      },
      { ok: true, value: currentDetail }
    ]
    renderView()
    const dialog = await openView()

    expect(await within(dialog).findByText('Unable to load workflow details')).toBeTruthy()
    expect(within(dialog).getByText('The workflow details could not be loaded. Try again.')).toBeTruthy()
    expect(within(dialog).queryByText('coordinator_unavailable')).toBeNull()
    expectDisabledRunReason(dialog, 'Run is unavailable until workflow readiness loads successfully.')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Retry' }))
    expect(await within(dialog).findByTestId('shared-mermaid-renderer')).toBeTruthy()
    expect(
      apiStructured.mock.calls.filter(
        ([request]) => request.path === `/api/plugins/workflow/workflows/${encodeURIComponent(WORKFLOW_NAME)}`
      )
    ).toHaveLength(2)
  })

  it('maps a typed missing-workflow error without exposing its raw code', async () => {
    detailResponses = [
      {
        body: { detail: { code: 'workflow_not_found', message: 'internal detail' } },
        ok: false,
        status: 404
      }
    ]
    renderView()
    const dialog = await openView()

    expect(await within(dialog).findByText('This workflow is no longer installed.')).toBeTruthy()
    expect(within(dialog).queryByText('workflow_not_found')).toBeNull()
    expect(within(dialog).queryByText('internal detail')).toBeNull()
  })

  it('disables Run while authoritative workflow detail is still loading', async () => {
    let resolveDetail!: (response: unknown) => void
    detailResponses = [
      new Promise(resolve => {
        resolveDetail = resolve
      })
    ]
    renderView()
    const dialog = await openView()
    const run = expectDisabledRunReason(dialog, 'Run is unavailable until workflow readiness finishes loading.')
    resolveDetail({ ok: true, value: currentDetail })
    await waitFor(() => expect(run.hasAttribute('disabled')).toBe(false))
  })

  it.each([
    [
      'untrusted',
      { trust_state: 'untrusted' as const },
      'Run is unavailable because this workflow failed trust verification.'
    ],
    [
      'unsupported inputs',
      { supported_inputs: { reason: 'unsupported_input_shape' as const, supported: false } },
      'Run is unavailable because this workflow uses unsupported input fields.'
    ],
    [
      'incompatible',
      { compatibility: { findings: [], level: 'unsupported', runnable: false } },
      'This workflow is not compatible with the current Hermes runtime and cannot start.'
    ],
    [
      'unhealthy coordinator',
      { coordinator: { healthy: false, reason: 'offline', status: 'unhealthy' } },
      "The background coordinator isn't running — try again shortly."
    ]
  ])('uses fetched detail authority to disable Run for %s workflows', async (_label, overrides, reason) => {
    currentDetail = detail(overrides)
    renderView()
    const dialog = await openView()

    await within(dialog).findByTestId('shared-mermaid-renderer')
    expectDisabledRunReason(dialog, reason)
  })

  it('enables Run when fetched detail is runnable despite a stale untrusted catalog row', async () => {
    currentCatalogDefinition = definition({ trust_state: 'untrusted' })
    currentDetail = detail({ trust_state: 'trusted' })
    renderView()
    const dialog = await openView()

    await within(dialog).findByTestId('shared-mermaid-renderer')
    expect(within(dialog).getByRole('button', { name: 'Run' }).hasAttribute('disabled')).toBe(false)
  })

  it('shows recursively stable redacted JSON read-only, copies it, and never refetches on toggles', async () => {
    renderView()
    const dialog = await openView()
    await within(dialog).findByTestId('shared-mermaid-renderer')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Definition' }))

    const expected = `{
  "alpha": "first",
  "nested": {
    "alpha": 1,
    "zulu": "[REDACTED]"
  },
  "steps": [
    {
      "alpha": 1,
      "zulu": 2
    }
  ],
  "zeta": 2
}`

    const source = await within(dialog).findByTestId('workflow-definition-json')
    expect(source.textContent).toBe(expected)
    expect(within(dialog).getByText('Redacted normalized definition — not the on-disk source.')).toBeTruthy()
    expect(source.closest('[data-slot="code-card"]')).toBeTruthy()
    expect(source.querySelector('textarea, input, [contenteditable="true"]')).toBeNull()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Copy definition' }))
    await waitFor(() => expect(writeClipboard).toHaveBeenCalledWith(expected))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Diagram' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Definition' }))
    expect(
      apiStructured.mock.calls.filter(
        ([request]) => request.path === `/api/plugins/workflow/workflows/${encodeURIComponent(WORKFLOW_NAME)}`
      )
    ).toHaveLength(1)
  })

  it('replaces View with Review & Run for the same captured profile without stacking dialogs or refetching', async () => {
    renderView()
    const viewDialog = await openView()
    await within(viewDialog).findByTestId('shared-mermaid-renderer')
    setApiRequestProfile('profile-b')
    fireEvent.click(within(viewDialog).getByRole('button', { name: 'Run' }))

    const review = await screen.findByRole('dialog', { name: `Review & Run ${WORKFLOW_NAME}` })
    expect(screen.getAllByRole('dialog')).toHaveLength(1)
    expect(screen.queryByRole('dialog', { name: `View ${WORKFLOW_NAME}` })).toBeNull()
    expect(within(review).getByText('Checks a release before deployment.')).toBeTruthy()
    expect(
      apiStructured.mock.calls.filter(
        ([request]) => request.path === `/api/plugins/workflow/workflows/${encodeURIComponent(WORKFLOW_NAME)}`
      )
    ).toHaveLength(1)
    fireEvent.click(within(review).getByRole('button', { name: 'Start workflow' }))
    await waitFor(() =>
      expect(apiStructured).toHaveBeenCalledWith(
        expect.objectContaining({ path: '/api/plugins/workflow/runs', profile: 'profile-a' })
      )
    )
  })
})
