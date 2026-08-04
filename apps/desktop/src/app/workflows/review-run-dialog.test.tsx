// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useKeybinds } from '@/app/hooks/use-keybinds'
import { setApiRequestProfile } from '@/hermes'
import { IS_MAC } from '@/lib/keybinds/combo'
import { $notifications, clearNotifications } from '@/store/notifications'
import type * as ProfileStore from '@/store/profile'
import type { WorkflowDefinition, WorkflowDetail, WorkflowRunSnapshot } from '@/types/hermes'

import { $workflowSelectedRunId } from './store'

import { WorkflowsView } from './index'

const WORKFLOW_NAME = 'Portable contract'
const IDEMPOTENCY_KEY = '11111111-2222-4333-8444-555555555555'
const profileRouting = vi.hoisted(() => ({ ensureGatewayProfile: vi.fn() }))

// Spread the REAL module and override only the side-effecting actions. An
// exhaustive hand-written mock breaks at collection time whenever upstream adds
// an export the code under test imports (v0.19.0 added normalizeProfileKey and
// $activeGatewayProfile, which is what broke this suite); spreading self-heals.
vi.mock('@/store/profile', async importOriginal => ({
  ...(await importOriginal<typeof ProfileStore>()),
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
    compatibility: { level: 'supported', runnable: true },
    description: 'Checks a release before deployment.',
    inputs: [],
    name: WORKFLOW_NAME,
    precedence: 2,
    run_support: { reason: 'supported', supported: true },
    source: 'profile',
    supported_inputs: { reason: 'parameterless', supported: true },
    trust_state: 'trusted',
    version: '1.0.0',
    ...overrides
  }
}

function detailWithoutProjection(
  projection: 'compatibility' | 'coordinator',
  representation: 'absent' | 'null' | 'undefined',
  overrides: Partial<WorkflowDetail> = {}
): WorkflowDetail {
  const payload = detail(overrides) as unknown as Record<string, unknown>

  if (representation === 'absent') {
    Reflect.deleteProperty(payload, projection)
  } else {
    payload[projection] = representation === 'null' ? null : undefined
  }

  return payload as unknown as WorkflowDetail
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
  useKeybinds({
    openNewSessionTab: vi.fn(),
    startFreshSession: vi.fn(),
    toggleCommandCenter: vi.fn(),
    toggleSelectedPin: vi.fn()
  })

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

      if (request.path.startsWith(`/api/plugins/workflow/workflows/${encodeURIComponent(WORKFLOW_NAME)}`)) {
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
    expect(within(dialog).getByRole('button', { name: 'Run later' })).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start workflow' }))

    await waitFor(() => {
      expect(apiStructured).toHaveBeenCalledWith({
        body: {
          catalog_source: 'profile',
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
    expect(apiStructured).toHaveBeenCalledWith({
      path: `/api/plugins/workflow/workflows/${encodeURIComponent(WORKFLOW_NAME)}?catalog_source=profile`,
      profile: 'profile-a'
    })
    expect((await screen.findByRole('tab', { name: 'Active board' })).getAttribute('aria-selected')).toBe('true')
    expect($workflowSelectedRunId.get()).toBe('run-created')
    expect($notifications.get()[0]?.message).toBe('Started')
  })

  it('reviews the server-authored language profile beside digest-bound trust and risk', async () => {
    catalogDefinition = definition({
      language: { effective_profile: 'hermes-legacy', legacy: true }
    })
    preflightHandler = async () => ({
      ok: true,
      value: detail({
        language: {
          declared_profile: 'archon-2026-07',
          effective_profile: 'archon-2026-07',
          legacy: false,
          normalized_definition_digest: 'd'.repeat(64),
          normalizer_version: 1
        },
        risk_summary: { package_digest: 'p'.repeat(64), risk_level: 'low' }
      })
    })
    renderView()

    const dialog = await openReviewDialog()
    expect(within(dialog).getByText('Archon 2026-07')).toBeTruthy()
    expect(within(dialog).getByText('Normalizer 1')).toBeTruthy()
    const digest = within(dialog).getByText('dddddddddddd…')
    expect(digest.getAttribute('title')).toBe('d'.repeat(64))
    expect(within(dialog).queryByText('Legacy semantics')).toBeNull()
    expect(within(dialog).getByText('p'.repeat(64))).toBeTruthy()
  })

  it('reviews additive v3 language and backend-authored findings', async () => {
    preflightHandler = async () => ({
      ok: true,
      value: detail({
        compatibility: {
          findings: [
            {
              blocking: true,
              code: 'legacy_language_profile',
              level: 'mapped',
              message: 'Backend-authored compatibility guidance',
              migration: 'Backend-authored migration guidance',
              path: 'sidecar.language_compatibility'
            }
          ],
          level: 'unsupported',
          runnable: false
        },
        language: {
          declared_profile: 'archon-2026-07',
          effective_profile: 'archon-2026-07',
          legacy: false,
          normalized_definition_digest: '3'.repeat(64),
          normalizer_version: 3
        }
      })
    })
    renderView()

    const dialog = await openReviewDialog()
    expect(within(dialog).getByText('Normalizer 3')).toBeTruthy()
    const message = within(dialog).getByText('Backend-authored compatibility guidance')
    expect(message).toBeTruthy()
    expect(within(message.closest('li')!).getByText('Backend-authored migration guidance')).toBeTruthy()
  })

  it('reviews a future server-authored workflow language profile without Archon relabeling', async () => {
    catalogDefinition = definition({
      language: { effective_profile: 'future-workflow-language' as never, legacy: false }
    })
    preflightHandler = async () => ({
      ok: true,
      value: detail({
        language: { effective_profile: 'future-workflow-language' as never, legacy: false }
      })
    })
    renderView()

    const dialog = await openReviewDialog()
    expect(within(dialog).getByText('future-workflow-language')).toBeTruthy()
    expect(within(dialog).queryByText('Archon 2026-07')).toBeNull()
  })

  it('derives schedule eligibility from run support and normalizes the local picker instant', async () => {
    catalogDefinition = definition({
      run_support: { reason: 'schedule_required', supported: false },
      source: 'showcase',
      trust_state: 'verified_bundled'
    })
    preflightHandler = async () => ({
      ok: true,
      value: detail({
        ...catalogDefinition,
        definition: { inputs: {}, name: WORKFLOW_NAME }
      })
    })
    renderView()

    const dialog = await openReviewDialog()
    const immediate = within(dialog).getByRole('button', { name: 'Start workflow' }) as HTMLButtonElement
    expect(immediate.disabled).toBe(true)
    const picker = within(dialog).getByLabelText('Run at') as HTMLInputElement
    fireEvent.change(picker, { target: { value: '2099-01-02T04:04' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Run later' }))

    const canonical = new Date(2099, 0, 2, 4, 4).toISOString().replace('.000Z', 'Z')
    await waitFor(() =>
      expect(apiStructured).toHaveBeenCalledWith(
        expect.objectContaining({
          body: expect.objectContaining({
            schedule_at: canonical,
            workflow: WORKFLOW_NAME
          }),
          method: 'POST',
          path: '/api/plugins/workflow/runs'
        })
      )
    )
    expect($notifications.get()[0]?.message).toBe('Scheduled')
  })

  it.each([
    ['', 'Choose a future date and time.'],
    ['2020-01-02T04:04', 'Choose a future date and time.']
  ])('blocks invalid Run later picker value %j with accessible localized feedback', async (value, message) => {
    renderView()
    const dialog = await openReviewDialog()
    const picker = within(dialog).getByLabelText('Run at')

    if (value) {
      fireEvent.change(picker, { target: { value } })
    }

    fireEvent.click(within(dialog).getByRole('button', { name: 'Run later' }))

    expect((await within(dialog).findByRole('alert')).textContent).toContain(message)
    expect(apiStructured.mock.calls.filter(([request]) => request.path === '/api/plugins/workflow/runs')).toHaveLength(
      0
    )
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
        catalog_source: 'profile',
        concurrency_policy: 'queue',
        idempotency_key: IDEMPOTENCY_KEY,
        values: { count: '3', enabled: 'false', mode: 'safe', title: 'release' },
        workflow: WORKFLOW_NAME
      },
      profile: 'profile-a'
    })
  })

  it('renders declared text and bundled fixture inputs and sends only the public text value', async () => {
    catalogDefinition = definition({
      inputs: [
        { max_bytes: 5, name: 'symptom', required: true, type: 'text' },
        { name: 'evidence', required: true, type: 'file' }
      ],
      source: 'showcase',
      supported_inputs: { reason: 'flat_inputs', supported: true },
      trust_state: 'verified_bundled'
    })
    preflightHandler = async () => ({ ok: true, value: detail({ ...catalogDefinition }) })
    renderView()

    const dialog = await openReviewDialog()
    const symptom = within(dialog).getByRole('textbox', { name: 'symptom' })
    const fixture = within(dialog).getByRole('group', { name: 'evidence' })

    expect(symptom.tagName).toBe('TEXTAREA')
    expect(within(dialog).getByText('0 / 5 bytes')).toBeTruthy()
    expect(within(fixture).getByText('Bundled fixture')).toBeTruthy()
    expect(fixture.querySelector('input, textarea, button, [contenteditable="true"]')).toBeNull()

    fireEvent.change(symptom, { target: { value: 'ééa' } })
    expect(within(dialog).getByText('5 / 5 bytes')).toBeTruthy()
    const submit = within(dialog).getByRole('button', { name: 'Start workflow' }) as HTMLButtonElement
    expect(submit.disabled).toBe(false)
    fireEvent.click(submit)

    await waitFor(() => expect($workflowSelectedRunId.get()).toBe('run-created'))
    const request = apiStructured.mock.calls.find(([candidate]) => candidate.path === '/api/plugins/workflow/runs')?.[0]
    expect(request?.body?.values).toEqual({ symptom: 'ééa' })
    expect(request?.body?.values).not.toHaveProperty('arguments')
    expect(request?.body?.values).not.toHaveProperty('evidence')
  })

  it('counts declared text as UTF-8 bytes and blocks a one-byte-over value without a POST', async () => {
    catalogDefinition = definition({
      inputs: [{ max_bytes: 5, name: 'symptom', required: true, type: 'text' }],
      source: 'showcase',
      supported_inputs: { reason: 'flat_inputs', supported: true },
      trust_state: 'verified_bundled'
    })
    preflightHandler = async () => ({ ok: true, value: detail({ ...catalogDefinition }) })
    renderView()

    const dialog = await openReviewDialog()
    const symptom = within(dialog).getByRole('textbox', { name: 'symptom' })
    fireEvent.change(symptom, { target: { value: 'ééab' } })

    expect(within(dialog).getByText('6 / 5 bytes')).toBeTruthy()
    expect(within(dialog).getByText('symptom exceeds the 5-byte limit.')).toBeTruthy()
    const submit = within(dialog).getByRole('button', { name: 'Start workflow' }) as HTMLButtonElement
    expect(submit.disabled).toBe(true)
    fireEvent.click(submit)
    submit.removeAttribute('disabled')
    fireEvent.click(submit)
    expect(apiStructured.mock.calls.filter(([request]) => request.path === '/api/plugins/workflow/runs')).toHaveLength(
      0
    )
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
    },
    {
      body: { detail: { code: 'workflow_showcase_cli_required', retryable: false } },
      copy: 'Run this bundled showcase from the CLI.',
      status: 409
    },
    {
      body: { detail: { code: 'workflow_showcase_verification_failed', retryable: false } },
      copy: 'The bundled showcase could not be verified and was not started.',
      status: 409
    },
    {
      body: { detail: { code: 'workflow_catalog_source_invalid', retryable: false } },
      copy: 'This workflow source is no longer available. Close this review and select it again.',
      status: 422
    }
  ])('shows the distinct $status admission failure without navigating', async ({ body, copy, status }) => {
    startHandler = async () => ({ body, ok: false, status })
    renderView()

    const dialog = await openReviewDialog()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start workflow' }))

    expect(await within(dialog).findByText(copy)).toBeTruthy()
    expect(within(dialog).queryByText('The workflow inputs were not accepted.')).toBeNull()
    expect(within(dialog).queryByRole('button', { name: 'Retry' })).toBeNull()
    expect(screen.getByRole('tab', { hidden: true, name: 'Workflows' }).getAttribute('aria-selected')).toBe('true')
    expect($workflowSelectedRunId.get()).toBeNull()
  })

  it('renders the server-authoritative typed schedule rejection on a Run later race', async () => {
    startHandler = async () => ({
      body: { detail: { code: 'workflow_schedule_invalid', retryable: false } },
      ok: false,
      status: 422
    })
    renderView()

    const dialog = await openReviewDialog()
    fireEvent.change(within(dialog).getByLabelText('Run at'), { target: { value: '2099-01-02T04:04' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Run later' }))

    expect(await within(dialog).findByText('Choose a future date and time.')).toBeTruthy()
    expect(within(dialog).queryByText('The workflow inputs were not accepted.')).toBeNull()
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

  it('keeps an existing disposition authoritative for Run later copy', async () => {
    catalogDefinition = definition({
      run_support: { reason: 'schedule_required', supported: false },
      source: 'showcase',
      trust_state: 'verified_bundled'
    })
    preflightHandler = async () => ({
      ok: true,
      value: detail({
        ...catalogDefinition,
        definition: { inputs: {}, name: WORKFLOW_NAME }
      })
    })
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
    fireEvent.change(within(dialog).getByLabelText('Run at'), { target: { value: '2099-01-02T04:04' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Run later' }))

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
        run_support: { reason: 'unsupported_inputs', supported: false },
        source: 'showcase',
        supported_inputs: { reason: 'unsupported_input_shape', supported: false }
      })
    })
    renderView()

    const dialog = await openReviewDialog()
    expect(
      within(dialog).getByText('Run is unavailable because this workflow uses unsupported input fields.')
    ).toBeTruthy()
    expect(within(dialog).getByText("The background coordinator isn't running — try again shortly.")).toBeTruthy()
    expect(within(dialog).queryByRole('combobox', { name: 'mode' })).toBeNull()
    expect((within(dialog).getByRole('button', { name: 'Start workflow' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it.each(['absent', 'undefined', 'null'] as const)(
    'fails closed without throwing or posting when review compatibility is %s',
    async representation => {
      preflightHandler = async () => ({
        ok: true,
        value: detailWithoutProjection('compatibility', representation)
      })
      renderView()

      const dialog = await openReviewDialog()
      expect(
        within(dialog).getByText('This workflow is not compatible with the current Hermes runtime and cannot start.')
      ).toBeTruthy()
      expect((within(dialog).getByRole('button', { name: 'Start workflow' }) as HTMLButtonElement).disabled).toBe(true)
      expect(
        apiStructured.mock.calls.filter(([request]) => request.path === '/api/plugins/workflow/runs')
      ).toHaveLength(0)
    }
  )

  it.each(['absent', 'undefined', 'null'] as const)(
    'fails closed without throwing or posting when review coordinator is %s',
    async representation => {
      preflightHandler = async () => ({
        ok: true,
        value: detailWithoutProjection('coordinator', representation)
      })
      renderView()

      const dialog = await openReviewDialog()
      expect(within(dialog).getByText("The background coordinator isn't running — try again shortly.")).toBeTruthy()
      expect((within(dialog).getByRole('button', { name: 'Start workflow' }) as HTMLButtonElement).disabled).toBe(true)
      expect(
        apiStructured.mock.calls.filter(([request]) => request.path === '/api/plugins/workflow/runs')
      ).toHaveLength(0)
    }
  )

  it('keeps compatibility failure ahead of missing trust in review skew', async () => {
    preflightHandler = async () => ({
      ok: true,
      value: detailWithoutProjection('compatibility', 'absent', { trust_state: undefined as never })
    })
    renderView()

    const dialog = await openReviewDialog()
    expect(
      within(dialog).getByText('This workflow is not compatible with the current Hermes runtime and cannot start.')
    ).toBeTruthy()
    expect((within(dialog).getByRole('button', { name: 'Start workflow' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('trusts verified bundles but blocks a showcase when authoritative detail requires the CLI', async () => {
    catalogDefinition = definition({ source: 'showcase', trust_state: 'verified_bundled' })
    preflightHandler = async () => ({
      ok: true,
      value: detail({
        run_support: { reason: 'showcase_cli_required', supported: false },
        source: 'showcase',
        trust_state: 'verified_bundled'
      })
    })
    renderView()

    const dialog = await openReviewDialog()
    expect(within(dialog).getByText('verified bundle')).toBeTruthy()
    expect(within(dialog).getByText('Run this bundled showcase from the CLI.')).toBeTruthy()
    expect((within(dialog).getByRole('button', { name: 'Start workflow' }) as HTMLButtonElement).disabled).toBe(true)
    expect(apiStructured.mock.calls.filter(([request]) => request.path === '/api/plugins/workflow/runs')).toHaveLength(
      0
    )
  })

  it('fails closed without crashing when an older backend omits detail run support', async () => {
    preflightHandler = async () => ({ ok: true, value: detail({ run_support: undefined as never }) })
    renderView()

    const dialog = await openReviewDialog()
    expect(
      within(dialog).getByText('Run is unavailable until the Hermes backend supports this workflow catalog version.')
    ).toBeTruthy()
    expect((within(dialog).getByRole('button', { name: 'Start workflow' }) as HTMLButtonElement).disabled).toBe(true)
    expect(apiStructured.mock.calls.filter(([request]) => request.path === '/api/plugins/workflow/runs')).toHaveLength(
      0
    )
  })

  it('keeps Run disabled for an unknown backend run-support reason', async () => {
    preflightHandler = async () => ({
      ok: true,
      value: detail({
        compatibility: { findings: [], level: 'unsupported', runnable: false },
        run_support: { reason: 'future_backend_rule' as never, supported: false },
        trust_state: 'trusted'
      })
    })
    renderView()

    const dialog = await openReviewDialog()
    expect(
      within(dialog).getByText('Run is unavailable until the Hermes backend supports this workflow catalog version.')
    ).toBeTruthy()
    expect((within(dialog).getByRole('button', { name: 'Start workflow' }) as HTMLButtonElement).disabled).toBe(true)
    expect(apiStructured.mock.calls.filter(([request]) => request.path === '/api/plugins/workflow/runs')).toHaveLength(
      0
    )
  })

  it('shows generic support copy for an incoherent supported unknown reason', async () => {
    preflightHandler = async () => ({
      ok: true,
      value: detail({
        run_support: { reason: 'future_backend_rule' as never, supported: true }
      })
    })
    renderView()

    const dialog = await openReviewDialog()
    expect(
      within(dialog).getByText('Run is unavailable until the Hermes backend supports this workflow catalog version.')
    ).toBeTruthy()
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

  it('does not derive Run eligibility from definition fields or finding codes', async () => {
    preflightHandler = async () => ({
      ok: true,
      value: detail({
        compatibility: {
          findings: [
            {
              blocking: true,
              code: 'server-only-finding',
              level: 'unsupported',
              message: 'Server-authored advisory',
              path: 'nodes[0].timeout'
            }
          ],
          level: 'unsupported',
          runnable: true
        },
        definition: { name: WORKFLOW_NAME, nodes: [{ id: 'start', timeout: 5 }] }
      })
    })
    renderView()

    const dialog = await openReviewDialog()
    expect((within(dialog).getByRole('button', { name: 'Start workflow' }) as HTMLButtonElement).disabled).toBe(false)
    expect(within(dialog).queryByText('Server-authored advisory')).toBeNull()
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
