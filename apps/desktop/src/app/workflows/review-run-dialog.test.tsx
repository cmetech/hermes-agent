// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useKeybinds } from '@/app/hooks/use-keybinds'
import { setApiRequestProfile } from '@/hermes'
import { IS_MAC } from '@/lib/keybinds/combo'
import { $notifications, clearNotifications } from '@/store/notifications'
import type { WorkflowDefinition, WorkflowDetail, WorkflowRunSnapshot } from '@/types/hermes'

import { $workflowSelectedRunId } from './store'

import { WorkflowsView } from './index'

const WORKFLOW_NAME = 'Portable contract'
const IDEMPOTENCY_KEY = '11111111-2222-4333-8444-555555555555'
const profileRouting = vi.hoisted(() => ({ ensureGatewayProfile: vi.fn() }))

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

let catalogDefinition: WorkflowDefinition
let preflightHandler: (request: StructuredRequest) => Promise<unknown>
let startHandler: (request: StructuredRequest) => Promise<unknown>

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
    definition: { inputs: {}, name: WORKFLOW_NAME },
    risk_summary: { execution_environment: 'local', risk_level: 'low' },
    topology: { mermaid: null, omitted: null, text: 'start', warnings: [] },
    ...overrides
  }
}

function runSnapshot(runId = 'run-created'): WorkflowRunSnapshot {
  return {
    health: 'healthy',
    next_actions: [],
    progress: { completed_nodes: 0, kind: 'graph', total_nodes: 1 },
    run_id: runId,
    state_version: 1,
    status: 'queued',
    updated_at: '2026-07-19T00:00:00Z',
    workflow: WORKFLOW_NAME
  }
}

function startResponse(disposition: 'created' | 'existing' = 'created', runId = 'run-created') {
  return {
    ok: true,
    value: {
      error: null,
      ok: true,
      result: {
        admission_disposition: disposition,
        blocked_by_run_id: null,
        queue_position: 1,
        run_id: runId,
        status: 'queued'
      },
      schema_version: 1
    }
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void

  const promise = new Promise<T>(onResolve => {
    resolve = onResolve
  })

  return { promise, resolve }
}

function renderView() {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false }, queries: { retry: false } } })

  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={['/workflows']}>
        <KeybindRouteHarness />
      </MemoryRouter>
    </QueryClientProvider>
  )
}

function KeybindRouteHarness() {
  useKeybinds({ startFreshSession: vi.fn(), toggleCommandCenter: vi.fn(), toggleSelectedPin: vi.fn() })

  return (
    <Routes>
      <Route element={<WorkflowsView />} path="/workflows" />
      <Route element={<p>Settings destination</p>} path="/settings" />
    </Routes>
  )
}

function pressSettingsShortcut() {
  fireEvent.keyDown(window, { code: 'Comma', ctrlKey: !IS_MAC, key: ',', metaKey: IS_MAC })
}

async function openReviewDialog() {
  fireEvent.click(await screen.findByRole('button', { name: 'Run' }))

  return screen.findByRole('dialog', { name: `Review & Run ${WORKFLOW_NAME}` })
}

describe('Review & Run workflow dialog', () => {
  const api = vi.fn()
  const apiStructured = vi.fn()

  beforeEach(() => {
    setApiRequestProfile('profile-a')
    profileRouting.ensureGatewayProfile.mockImplementation(async profile => setApiRequestProfile(profile))
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' })
    Object.defineProperty(globalThis.crypto, 'randomUUID', {
      configurable: true,
      value: vi.fn(() => IDEMPOTENCY_KEY)
    })
    Object.defineProperties(HTMLElement.prototype, {
      hasPointerCapture: { configurable: true, value: vi.fn(() => false) },
      releasePointerCapture: { configurable: true, value: vi.fn() },
      scrollIntoView: { configurable: true, value: vi.fn() },
      setPointerCapture: { configurable: true, value: vi.fn() }
    })
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: { api, apiStructured }
    })
    api.mockImplementation(async request => {
      if (request.path.startsWith('/api/plugins/workflow/runs?')) {
        return { next_cursor: null, runs: [runSnapshot()], schema_version: 1 }
      }

      if (request.path === '/api/plugins/workflow/attention') {
        return { items: [], next_cursor: null, schema_version: 1 }
      }

      if (request.path === '/api/plugins/workflow/runs/run-created') {
        return runSnapshot()
      }

      throw new Error(`unexpected legacy request: ${request.path}`)
    })
    catalogDefinition = definition()
    preflightHandler = async () => ({ ok: true, value: detail() })
    startHandler = async () => startResponse()
    apiStructured.mockImplementation(async (request: StructuredRequest) => {
      if (request.path === '/api/plugins/workflow/workflows') {
        return { ok: true, value: { items: [catalogDefinition], truncated: false } }
      }

      if (request.path === `/api/plugins/workflow/workflows/${encodeURIComponent(WORKFLOW_NAME)}`) {
        return preflightHandler(request)
      }

      if (request.path === '/api/plugins/workflow/runs') {
        return startHandler(request)
      }

      throw new Error(`unexpected structured request: ${request.path}`)
    })
    $workflowSelectedRunId.set(null)
    clearNotifications()
  })

  afterEach(() => {
    cleanup()
    setApiRequestProfile(null)
    api.mockReset()
    apiStructured.mockReset()
    profileRouting.ensureGatewayProfile.mockReset()
    $workflowSelectedRunId.set(null)
    clearNotifications()
    Reflect.deleteProperty(window, 'hermesDesktop')
    vi.restoreAllMocks()
  })

  it('preflights, submits the exact parameterless POST without provenance, and highlights the created run', async () => {
    renderView()
    const dialog = await openReviewDialog()
    expect(within(dialog).getByText('Checks a release before deployment.')).toBeTruthy()
    expect(within(dialog).queryByRole('group', { name: 'Inputs' })).toBeNull()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start workflow' }))

    await waitFor(() => {
      expect(apiStructured).toHaveBeenCalledWith({
        body: {
          concurrency_policy: 'queue',
          idempotency_key: IDEMPOTENCY_KEY,
          values: {},
          workflow: WORKFLOW_NAME
        },
        method: 'POST',
        path: '/api/plugins/workflow/runs',
        profile: 'profile-a'
      })
    })

    const postBody = apiStructured.mock.calls.find(([request]) => request.path === '/api/plugins/workflow/runs')?.[0]
      .body

    expect(postBody).not.toHaveProperty('provenance')
    expect(postBody).not.toHaveProperty('source')
    expect((await screen.findByRole('tab', { name: 'Active board' })).getAttribute('aria-selected')).toBe('true')
    expect($workflowSelectedRunId.get()).toBe('run-created')
    expect($notifications.get()[0]?.message).toBe('Started')
  })

  it('uses typed controls, serializes flat values as strings, and binds preflight plus start to the opening profile', async () => {
    catalogDefinition = definition({
      inputs: [
        { name: 'title', required: true, type: 'string' },
        { name: 'count', required: true, type: 'number' },
        { name: 'enabled', required: false, type: 'boolean' },
        { name: 'mode', required: true, type: 'enum' }
      ],
      supported_inputs: { reason: 'flat_inputs', supported: true }
    })
    preflightHandler = async () => ({
      ok: true,
      value: detail({
        ...catalogDefinition,
        definition: {
          inputs: {
            count: { required: true, type: 'number' },
            enabled: { required: false, type: 'boolean' },
            mode: { required: true, type: 'enum', values: ['safe', 'fast'] },
            title: { required: true, type: 'string' }
          }
        }
      })
    })
    renderView()

    const dialog = await openReviewDialog()
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'title' }), { target: { value: 'release' } })
    fireEvent.change(within(dialog).getByRole('spinbutton', { name: 'count' }), { target: { value: '3' } })
    const enabled = within(dialog).getByRole('combobox', { name: 'enabled' })
    expect(enabled.textContent).toContain('Not set')
    fireEvent.pointerDown(enabled, { button: 0, ctrlKey: false, pointerType: 'mouse' })
    fireEvent.click(await screen.findByRole('option', { name: 'Off' }))
    const mode = within(dialog).getByRole('combobox', { name: 'mode' })
    fireEvent.pointerDown(mode, { button: 0, ctrlKey: false, pointerType: 'mouse' })
    fireEvent.click(await screen.findByRole('option', { name: 'safe' }))
    setApiRequestProfile('profile-b')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start workflow' }))

    await waitFor(() => expect($workflowSelectedRunId.get()).toBe('run-created'))
    const requests = apiStructured.mock.calls.map(([request]) => request as StructuredRequest)
    expect(requests.find(request => request.path.includes('/workflows/Portable%20contract'))?.profile).toBe('profile-a')
    expect(requests.find(request => request.path === '/api/plugins/workflow/runs')).toMatchObject({
      body: {
        concurrency_policy: 'queue',
        idempotency_key: IDEMPOTENCY_KEY,
        values: { count: '3', enabled: 'false', mode: 'safe', title: 'release' },
        workflow: WORKFLOW_NAME
      },
      profile: 'profile-a'
    })
  })

  it('omits every untouched optional flat input from the admission body', async () => {
    catalogDefinition = definition({
      inputs: [
        { name: 'title', required: false, type: 'string' },
        { name: 'count', required: false, type: 'number' },
        { name: 'enabled', required: false, type: 'boolean' },
        { name: 'mode', required: false, type: 'enum' }
      ],
      supported_inputs: { reason: 'flat_inputs', supported: true }
    })
    preflightHandler = async () => ({
      ok: true,
      value: detail({
        ...catalogDefinition,
        definition: {
          inputs: {
            count: { required: false, type: 'number' },
            enabled: { required: false, type: 'boolean' },
            mode: { required: false, type: 'enum', values: ['safe', 'fast'] },
            title: { required: false, type: 'string' }
          }
        }
      })
    })
    renderView()

    const dialog = await openReviewDialog()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start workflow' }))

    await waitFor(() => expect($workflowSelectedRunId.get()).toBe('run-created'))
    const request = apiStructured.mock.calls.find(([candidate]) => candidate.path === '/api/plugins/workflow/runs')?.[0]
    expect(request?.body?.values).toEqual({})
  })

  it('coalesces a rapid double-submit into one in-flight POST with the modal key', async () => {
    const pending = deferred<unknown>()
    const post = vi.fn((_request: StructuredRequest) => pending.promise)
    startHandler = post
    renderView()

    const dialog = await openReviewDialog()
    const submit = within(dialog).getByRole('button', { name: 'Start workflow' })
    fireEvent.click(submit)
    fireEvent.click(submit)

    expect(post).toHaveBeenCalledTimes(1)
    expect(post.mock.calls[0]?.[0].body?.idempotency_key).toBe(IDEMPOTENCY_KEY)
    pending.resolve(startResponse())
    await waitFor(() => expect($workflowSelectedRunId.get()).toBe('run-created'))
  })

  it('cannot dismiss an unresolved admission and settles its original intent exactly once', async () => {
    const pending = deferred<unknown>()
    const post = vi.fn((_request: StructuredRequest) => pending.promise)
    startHandler = post
    renderView()

    const dialog = await openReviewDialog()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start workflow' }))
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1))

    expect(within(dialog).queryByRole('button', { name: 'Close' })).toBeNull()
    fireEvent.keyDown(document, { key: 'Escape' })
    const overlay = document.querySelector<HTMLElement>('[data-slot="dialog-overlay"]')
    expect(overlay).toBeTruthy()
    fireEvent.pointerDown(overlay!)
    fireEvent.click(overlay!)
    expect(screen.getByRole('dialog', { name: `Review & Run ${WORKFLOW_NAME}` })).toBeTruthy()
    pressSettingsShortcut()
    expect(screen.queryByText('Settings destination')).toBeNull()
    expect(screen.getByRole('dialog', { name: `Review & Run ${WORKFLOW_NAME}` })).toBeTruthy()

    pending.resolve(startResponse())
    await waitFor(() => expect($workflowSelectedRunId.get()).toBe('run-created'))
    expect(post).toHaveBeenCalledTimes(1)
    expect(post.mock.calls[0]?.[0].body?.idempotency_key).toBe(IDEMPOTENCY_KEY)
    expect($notifications.get()).toHaveLength(1)
    expect(
      api.mock.calls.filter(([request]) => request.path === '/api/plugins/workflow/runs/run-created')
    ).toHaveLength(1)
  })

  it('activates the captured profile before fetching and highlighting the admitted run', async () => {
    const activation = deferred<void>()
    profileRouting.ensureGatewayProfile.mockImplementation(async profile => {
      await activation.promise
      setApiRequestProfile(profile)
    })
    renderView()

    const dialog = await openReviewDialog()
    setApiRequestProfile('profile-b')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start workflow' }))

    await waitFor(() => expect(profileRouting.ensureGatewayProfile).toHaveBeenCalledWith('profile-a'))
    expect(screen.getByRole('tab', { hidden: true, name: 'Workflows' }).getAttribute('aria-selected')).toBe('true')
    expect(api.mock.calls.filter(([request]) => request.profile === 'profile-b')).toHaveLength(0)
    pressSettingsShortcut()
    expect(screen.queryByText('Settings destination')).toBeNull()
    expect(screen.getByRole('dialog', { name: `Review & Run ${WORKFLOW_NAME}` })).toBeTruthy()

    activation.resolve()
    const inspector = await screen.findByRole('complementary', { name: `${WORKFLOW_NAME} run inspector` })
    expect(within(inspector).getByText('run-created')).toBeTruthy()
    expect($workflowSelectedRunId.get()).toBe('run-created')

    const runRequests = api.mock.calls
      .map(([request]) => request as StructuredRequest)
      .filter(request => request.path.includes('/api/plugins/workflow/runs'))

    expect(runRequests.length).toBeGreaterThan(0)
    expect(runRequests.every(request => request.profile === 'profile-a')).toBe(true)
    expect(runRequests.some(request => request.path === '/api/plugins/workflow/runs/run-created')).toBe(true)
    const posts = apiStructured.mock.calls.filter(([request]) => request.path === '/api/plugins/workflow/runs')
    expect(posts).toHaveLength(1)
    expect(posts[0]?.[0].body?.idempotency_key).toBe(IDEMPOTENCY_KEY)
    expect($notifications.get()).toHaveLength(1)
  })

  it('does not apply locate side effects after an external unmount during profile activation', async () => {
    const activation = deferred<void>()
    profileRouting.ensureGatewayProfile.mockImplementation(async profile => {
      await activation.promise
      setApiRequestProfile(profile)
    })
    const rendered = renderView()

    const dialog = await openReviewDialog()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start workflow' }))
    await waitFor(() => expect(profileRouting.ensureGatewayProfile).toHaveBeenCalledWith('profile-a'))

    rendered.unmount()
    activation.resolve()
    await activation.promise
    await new Promise(resolve => setTimeout(resolve, 0))

    expect($workflowSelectedRunId.get()).toBeNull()
    expect($notifications.get()).toHaveLength(0)
    const posts = apiStructured.mock.calls.filter(([request]) => request.path === '/api/plugins/workflow/runs')
    expect(posts).toHaveLength(1)
    expect(posts[0]?.[0].body?.idempotency_key).toBe(IDEMPOTENCY_KEY)
  })

  it('retries profile activation for the admitted run without posting or allowing input edits again', async () => {
    catalogDefinition = definition({
      inputs: [{ name: 'title', required: true, type: 'string' }],
      supported_inputs: { reason: 'flat_inputs', supported: true }
    })
    preflightHandler = async () => ({ ok: true, value: detail({ ...catalogDefinition }) })

    const post = vi
      .fn()
      .mockResolvedValueOnce(startResponse())
      .mockResolvedValueOnce({
        body: { detail: { code: 'coordinator_unavailable', retryable: true } },
        ok: false,
        status: 503
      })

    startHandler = post
    profileRouting.ensureGatewayProfile
      .mockRejectedValueOnce(new Error('profile unavailable'))
      .mockImplementationOnce(async profile => setApiRequestProfile(profile))
    renderView()

    const dialog = await openReviewDialog()
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'title' }), { target: { value: 'release' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start workflow' }))

    expect(
      await within(dialog).findByText('The run started, but its profile could not be opened. Retry to locate it.')
    ).toBeTruthy()
    expect(within(dialog).queryByText('The run could not be started because the connection failed.')).toBeNull()
    expect(within(dialog).queryByRole('textbox', { name: 'title' })).toBeNull()
    expect(within(dialog).queryByRole('button', { name: 'Start workflow' })).toBeNull()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Retry' }))

    await waitFor(() => expect($workflowSelectedRunId.get()).toBe('run-created'))
    expect(post).toHaveBeenCalledTimes(1)
    expect(post.mock.calls[0]?.[0].body.idempotency_key).toBe(IDEMPOTENCY_KEY)
    expect(profileRouting.ensureGatewayProfile).toHaveBeenCalledTimes(2)
  })

  it('uses one UUID for a modal lifetime across a coordinator retry', async () => {
    const post = vi
      .fn()
      .mockResolvedValueOnce({
        body: { detail: { code: 'coordinator_unavailable', retryable: true } },
        ok: false,
        status: 503
      })
      .mockResolvedValueOnce(startResponse())

    startHandler = post
    renderView()

    const dialog = await openReviewDialog()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start workflow' }))
    expect(
      await within(dialog).findByText("The background coordinator isn't running — try again shortly.")
    ).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Retry' }))

    await waitFor(() => expect(post).toHaveBeenCalledTimes(2))
    expect(post.mock.calls.map(([request]) => request.body.idempotency_key)).toEqual([IDEMPOTENCY_KEY, IDEMPOTENCY_KEY])
    expect(globalThis.crypto.randomUUID).toHaveBeenCalledTimes(1)
  })

  it('reuses the same UUID after a transport failure instead of duplicating intent', async () => {
    const post = vi.fn().mockRejectedValueOnce(new Error('socket closed')).mockResolvedValueOnce(startResponse())
    startHandler = post
    renderView()

    const dialog = await openReviewDialog()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start workflow' }))
    expect(await within(dialog).findByText('The run could not be started because the connection failed.')).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Retry' }))

    await waitFor(() => expect(post).toHaveBeenCalledTimes(2))
    expect(new Set(post.mock.calls.map(([request]) => request.body.idempotency_key))).toEqual(
      new Set([IDEMPOTENCY_KEY])
    )
  })

  it.each([
    {
      body: { detail: { code: 'idempotency_conflict', message: 'changed intent', retryable: false } },
      copy: 'These inputs changed after this run intent was created. Close this review and try again.',
      status: 409
    }
  ])('shows the distinct $status admission failure without navigating', async ({ body, copy, status }) => {
    startHandler = async () => ({ body, ok: false, status })
    renderView()

    const dialog = await openReviewDialog()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start workflow' }))

    expect(await within(dialog).findByText(copy)).toBeTruthy()
    expect(screen.getByRole('tab', { hidden: true, name: 'Workflows' }).getAttribute('aria-selected')).toBe('true')
    expect($workflowSelectedRunId.get()).toBeNull()
  })

  it('maps the FastAPI 422 loc/msg array to the rejected input field', async () => {
    catalogDefinition = definition({
      inputs: [{ name: 'count', required: true, type: 'number' }],
      supported_inputs: { reason: 'flat_inputs', supported: true }
    })
    preflightHandler = async () => ({ ok: true, value: detail({ ...catalogDefinition }) })
    startHandler = async () => ({
      body: {
        detail: [
          {
            input: 3,
            loc: ['body', 'values', 'count'],
            msg: 'Input should be a valid string',
            type: 'string_type'
          }
        ]
      },
      ok: false,
      status: 422
    })
    renderView()

    const dialog = await openReviewDialog()
    const count = within(dialog).getByRole('spinbutton', { name: 'count' })
    fireEvent.change(count, { target: { value: '3' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start workflow' }))

    const messages = await within(dialog).findAllByText('Input should be a valid string')
    const message = messages.find(item => item.id === count.getAttribute('aria-describedby'))
    expect(message).toBeTruthy()
    expect(count.getAttribute('aria-describedby')).toBe(message!.id)
    expect(screen.getByRole('tab', { hidden: true, name: 'Workflows' }).getAttribute('aria-selected')).toBe('true')
  })

  it('surfaces an existing disposition and highlights that existing run', async () => {
    startHandler = async () => startResponse('existing', 'run-existing')
    api.mockImplementation(async request => {
      if (request.path.startsWith('/api/plugins/workflow/runs?')) {
        return { next_cursor: null, runs: [runSnapshot('run-existing')], schema_version: 1 }
      }

      if (request.path === '/api/plugins/workflow/attention') {
        return { items: [], next_cursor: null, schema_version: 1 }
      }

      if (request.path === '/api/plugins/workflow/runs/run-existing') {
        return runSnapshot('run-existing')
      }

      throw new Error(`unexpected legacy request: ${request.path}`)
    })
    renderView()

    const dialog = await openReviewDialog()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start workflow' }))

    await waitFor(() => expect($workflowSelectedRunId.get()).toBe('run-existing'))
    expect($notifications.get()[0]?.message).toBe('Already running — showing you that run')
  })

  it('fails closed when preflight reports unsupported inputs or an unavailable coordinator', async () => {
    preflightHandler = async () => ({
      ok: true,
      value: detail({
        coordinator: { healthy: false, reason: 'coordinator_missing', status: 'unavailable' },
        definition: { inputs: { mode: { required: false, type: 'enum' } } },
        inputs: [{ name: 'mode', required: false, type: 'enum' }],
        supported_inputs: { reason: 'unsupported_input_shape', supported: false }
      })
    })
    renderView()

    const dialog = await openReviewDialog()
    expect(
      within(dialog).getByText(
        "This workflow's inputs aren't supported in the app yet — run it with hermes workflow run Portable contract."
      )
    ).toBeTruthy()
    expect(within(dialog).getByText("The background coordinator isn't running — try again shortly.")).toBeTruthy()
    expect(within(dialog).queryByRole('combobox', { name: 'mode' })).toBeNull()
    expect((within(dialog).getByRole('button', { name: 'Start workflow' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('shows blocking compatibility findings and refuses to POST a non-runnable workflow', async () => {
    preflightHandler = async () => ({
      ok: true,
      value: detail({
        compatibility: {
          findings: [
            {
              blocking: true,
              code: 'compatibility',
              level: 'unsupported',
              message: 'provider custom does not advertise reasoning_effort',
              path: 'nodes[0].effort'
            }
          ],
          level: 'unsupported',
          runnable: false
        }
      })
    })
    renderView()

    const dialog = await openReviewDialog()
    expect(
      within(dialog).getByText('This workflow is not compatible with the current Hermes runtime and cannot start.')
    ).toBeTruthy()
    expect(within(dialog).getByText('provider custom does not advertise reasoning_effort')).toBeTruthy()
    expect((within(dialog).getByRole('button', { name: 'Start workflow' }) as HTMLButtonElement).disabled).toBe(true)
    expect(apiStructured.mock.calls.filter(([request]) => request.path === '/api/plugins/workflow/runs')).toHaveLength(
      0
    )
  })

  it('shows a global validation message when the rejected field is not rendered', async () => {
    startHandler = async () => ({
      body: {
        detail: [
          {
            loc: ['body', 'values', 'server_only'],
            msg: 'Server-only validation failed',
            type: 'value_error'
          }
        ]
      },
      ok: false,
      status: 422
    })
    renderView()

    const dialog = await openReviewDialog()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start workflow' }))

    expect(await within(dialog).findByText('Server-only validation failed')).toBeTruthy()
  })

  it('uses accessible dialog semantics, restores focus on close, and ignores stale preflight completion after reopen', async () => {
    const stale = deferred<unknown>()
    preflightHandler = vi
      .fn()
      .mockImplementationOnce(() => stale.promise)
      .mockResolvedValue({
        ok: true,
        value: detail({ description: 'Fresh review' })
      })
    renderView()

    const trigger = await screen.findByRole('button', { name: 'Run' })
    trigger.focus()
    fireEvent.click(trigger)
    const firstDialog = await screen.findByRole('dialog', { name: `Review & Run ${WORKFLOW_NAME}` })
    expect(firstDialog.getAttribute('aria-describedby')).toBeTruthy()
    fireEvent.click(within(firstDialog).getByRole('button', { name: 'Close' }))
    await waitFor(() => expect(document.activeElement).toBe(trigger))
    fireEvent.click(trigger)
    const secondDialog = await screen.findByRole('dialog', { name: `Review & Run ${WORKFLOW_NAME}` })
    expect(await within(secondDialog).findByText('Fresh review')).toBeTruthy()
    stale.resolve({ ok: true, value: detail({ description: 'Stale review' }) })
    await Promise.resolve()
    expect(within(secondDialog).queryByText('Stale review')).toBeNull()
  })
})
