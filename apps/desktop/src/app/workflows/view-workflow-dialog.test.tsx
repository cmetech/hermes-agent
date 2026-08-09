// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { openModalOwnsKeyboard } from '@/app/hooks/use-keybinds'
import { setApiRequestProfile } from '@/hermes'
import { TRANSLATIONS } from '@/i18n'
import type * as ProfileStore from '@/store/profile'
import type { WorkflowDefinition, WorkflowDetail } from '@/types/hermes'

import { workflowDetailQueryKey, workflowDetailQueryOptions } from './detail-query'

import { WorkflowsView } from './index'

const WORKFLOW_NAME = 'Portable contract'
const MERMAID_SOURCE = 'flowchart TD\n  start["Start"] --> finish["Finish"]'
const renderer = vi.hoisted(() => ({ calls: vi.fn<(code: string) => void>() }))
const profileRouting = vi.hoisted(() => ({ ensureGatewayProfile: vi.fn() }))
const workflowCopy = TRANSLATIONS.en.operations

vi.mock('@/components/assistant-ui/embeds/mermaid-embed', () => ({
  default: ({ code }: { code: string }) => {
    renderer.calls(code)

    if (code === 'renderer throws') {
      throw new Error('renderer failed')
    }

    return <div data-testid="shared-mermaid-renderer">{code}</div>
  }
}))

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

      if (request.path.startsWith(`/api/plugins/workflow/workflows/${encodeURIComponent(WORKFLOW_NAME)}`)) {
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
    expect(workflowDetailQueryOptions(WORKFLOW_NAME, 'profile', 'profile-a').retry).toBe(false)
    expect(workflowDetailQueryKey(WORKFLOW_NAME, 'project', 'profile-a')).not.toEqual(
      workflowDetailQueryKey(WORKFLOW_NAME, 'showcase', 'profile-a')
    )
  })

  it('fetches the captured profile and renders topology through the shared Mermaid component', async () => {
    renderView()
    const dialog = await openView()

    expect((await within(dialog).findByTestId('shared-mermaid-renderer')).textContent).toBe(MERMAID_SOURCE)
    expect(renderer.calls).toHaveBeenCalledWith(MERMAID_SOURCE)
    expect(apiStructured).toHaveBeenCalledWith({
      path: `/api/plugins/workflow/workflows/${encodeURIComponent(WORKFLOW_NAME)}?catalog_source=profile`,
      profile: 'profile-a'
    })
    expect(
      apiStructured.mock.calls.filter(([request]) =>
        request.path.startsWith(`/api/plugins/workflow/workflows/${encodeURIComponent(WORKFLOW_NAME)}`)
      )
    ).toHaveLength(1)
    expect(openModalOwnsKeyboard()).toBe(true)
    expect(dialog.querySelector('[data-workflow-view-scroll]')).toBeTruthy()
  })

  it('explains server-authored legacy semantics without inventing a profile', async () => {
    currentDetail = detail({
      language: {
        declared_profile: null,
        effective_profile: 'hermes-legacy',
        legacy: true,
        normalized_definition_digest: 'a'.repeat(64),
        normalizer_version: 1
      }
    })
    renderView()
    const dialog = await openView()

    expect(await within(dialog).findByText('Legacy semantics')).toBeTruthy()
    expect(
      within(dialog).getByText(
        workflowCopy.workflowLanguageLegacyDescription
      )
    ).toBeTruthy()
    expect(within(dialog).getByText('Normalizer 1')).toBeTruthy()
    const digest = within(dialog).getByText('aaaaaaaaaaaa…')
    expect(digest.getAttribute('title')).toBe('a'.repeat(64))
  })

  it('shows a future server-authored workflow language profile instead of Archon copy', async () => {
    currentDetail = detail({
      language: { effective_profile: 'future-workflow-language' as never, legacy: false }
    })
    renderView()
    const dialog = await openView()

    expect(await within(dialog).findByText('future-workflow-language')).toBeTruthy()
    expect(within(dialog).queryByText('Archon 2026-07')).toBeNull()
  })

  it('keeps an older backend detail usable when additive language fields are absent', async () => {
    currentDetail = detail({
      language: {
        effective_profile: 'archon-2026-07',
        legacy: false
      }
    })
    renderView()

    const dialog = await openView()
    expect(await within(dialog).findByText('Archon 2026-07')).toBeTruthy()
    expect(within(dialog).queryByText(/Normalizer/)).toBeNull()
  })

  it('renders only the backend-authored Phase 5 provider resolution', async () => {
    currentDetail = detail({
      language: {
        declared_profile: 'archon-2026-07',
        effective_profile: 'archon-2026-07',
        legacy: false,
        normalizer_version: 5
      },
      provider_capability: {
        authority_digest: 'd'.repeat(64),
        decisions: [],
        degraded_count: 0,
        level: 'portable',
        mixed_provider: false,
        resolved_route_count: 1,
        routes: [
          {
            inline_agent_id: null,
            model: 'openai/gpt-5.4',
            node_id: 'ask',
            provider: 'openrouter',
            reference_kind: 'configured_alias',
            role: 'primary'
          }
        ],
        schema_version: 1,
        unsupported_count: 0,
        warning_codes: []
      }
    })
    renderView()
    const dialog = await openView()
    const readiness = await within(dialog).findByRole('region', { name: 'Provider readiness' })

    expect(within(readiness).getByText('openrouter', { exact: false }).textContent).toContain('openai/gpt-5.4')
    expect(within(readiness).getByText('d'.repeat(64))).toBeTruthy()
  })

  it('catches authenticated dependency details being discarded or replaced with filesystem data', async () => {
    currentDetail = detail({
      compilation: {
        composite_digest: 'phase4-composite-digest',
        counts: {
          dependency_packages: 1,
          expanded_edges: 1,
          expanded_nodes: 2
        },
        dependencies: [
          {
            definition_location: '/private/workflows/child.yaml',
            package_key: 'profile:surface-child',
            workflow_name: 'surface-child'
          }
        ],
        include_depth: 1,
        ignored_policies: [
          {
            fields: ['execution_environment', 'required_secrets'],
            package_key: 'profile:surface-child',
            sidecar_digest: 'private-sidecar-digest'
          }
        ],
        sources: [
          {
            catalog_source: 'project',
            definition_location: '/private/workflows/root.yaml',
            package_key: 'project:surface-root',
            precedence: 1,
            workflow_name: 'surface-root'
          },
          {
            catalog_source: 'profile',
            definition_location: '/private/workflows/child.yaml',
            package_key: 'profile:surface-child',
            precedence: 2,
            workflow_name: 'surface-child'
          }
        ]
      }
    })
    renderView()
    const dialog = await openView()
    const dependencies = await within(dialog).findByRole('region', { name: 'Workflow dependencies' })

    expect(within(dependencies).getByText('surface-root')).toBeTruthy()
    expect(within(dependencies).getByText('Project')).toBeTruthy()
    expect(within(dependencies).getByText('surface-child')).toBeTruthy()
    expect(within(dependencies).getByText('Profile')).toBeTruthy()
    expect(within(dependencies).getAllByText('Precedence')).toHaveLength(2)
    expect(within(dependencies).getByText('Dependency packages').nextSibling?.textContent).toBe('1')
    expect(within(dependencies).getByText('Expanded nodes').nextSibling?.textContent).toBe('2')
    expect(within(dependencies).getByText('Expanded edges').nextSibling?.textContent).toBe('1')
    expect(within(dependencies).getByText('Include depth').nextSibling?.textContent).toBe('1')
    expect(within(dependencies).getByText('phase4-composite-digest')).toBeTruthy()
    expect(within(dependencies).getByText('Ignored: execution environment').closest('[data-slot="badge"]')).toBeTruthy()
    expect(within(dependencies).getByText('Ignored: required secrets').closest('[data-slot="badge"]')).toBeTruthy()
    expect(dependencies.textContent).not.toContain('/private/workflows')
    expect(dependencies.textContent).not.toContain('private-sidecar-digest')
  })

  it('catches older workflow details becoming unusable when compilation diagnostics are absent', async () => {
    currentDetail = detail()
    renderView()
    const dialog = await openView()

    expect(await within(dialog).findByTestId('shared-mermaid-renderer')).toBeTruthy()
    expect(within(dialog).queryByRole('region', { name: 'Workflow dependencies' })).toBeNull()
    expect(within(dialog).getByRole('button', { name: 'Run' }).hasAttribute('disabled')).toBe(false)
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
      apiStructured.mock.calls.filter(([request]) =>
        request.path.startsWith(`/api/plugins/workflow/workflows/${encodeURIComponent(WORKFLOW_NAME)}`)
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
      {
        run_support: { reason: 'unsupported_inputs' as const, supported: false },
        source: 'showcase' as const,
        supported_inputs: { reason: 'unsupported_input_shape' as const, supported: false }
      },
      'Run is unavailable because this workflow uses unsupported input fields.'
    ],
    [
      'incompatible',
      { compatibility: { findings: [], level: 'unsupported', runnable: false } },
      workflowCopy.workflowRunIncompatible
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

  it.each(['absent', 'undefined', 'null'] as const)(
    'fails closed without throwing when detail compatibility is %s',
    async representation => {
      currentDetail = detailWithoutProjection('compatibility', representation)
      renderView()
      const dialog = await openView()

      await within(dialog).findByTestId('shared-mermaid-renderer')
      expectDisabledRunReason(
        dialog,
        workflowCopy.workflowRunIncompatible
      )
    }
  )

  it.each(['absent', 'undefined', 'null'] as const)(
    'fails closed without throwing when detail coordinator is %s',
    async representation => {
      currentDetail = detailWithoutProjection('coordinator', representation)
      renderView()
      const dialog = await openView()

      await within(dialog).findByTestId('shared-mermaid-renderer')
      expectDisabledRunReason(dialog, "The background coordinator isn't running — try again shortly.")
    }
  )

  it('keeps compatibility failure ahead of missing trust in detail skew', async () => {
    currentDetail = detailWithoutProjection('compatibility', 'absent', { trust_state: undefined as never })
    renderView()
    const dialog = await openView()

    await within(dialog).findByTestId('shared-mermaid-renderer')
    expectDisabledRunReason(dialog, workflowCopy.workflowRunIncompatible)
  })

  it('enables Run when fetched detail is runnable despite a stale untrusted catalog row', async () => {
    currentCatalogDefinition = definition({ trust_state: 'untrusted' })
    currentDetail = detail({ trust_state: 'trusted' })
    renderView()
    const dialog = await openView()

    await within(dialog).findByTestId('shared-mermaid-renderer')
    expect(within(dialog).getByRole('button', { name: 'Run' }).hasAttribute('disabled')).toBe(false)
  })

  it('allows verified bundled detail to Run', async () => {
    currentCatalogDefinition = definition({ source: 'showcase', trust_state: 'verified_bundled' })
    currentDetail = detail({ source: 'showcase', trust_state: 'verified_bundled' })
    renderView()
    const dialog = await openView()

    await within(dialog).findByTestId('shared-mermaid-renderer')
    expect(within(dialog).getByRole('button', { name: 'Run' }).hasAttribute('disabled')).toBe(false)
  })

  it('obeys authoritative showcase eligibility from fetched detail', async () => {
    currentCatalogDefinition = definition({ source: 'showcase', trust_state: 'verified_bundled' })
    currentDetail = detail({
      run_support: { reason: 'showcase_cli_required', supported: false },
      source: 'showcase',
      trust_state: 'verified_bundled'
    })
    renderView()
    const dialog = await openView()

    await within(dialog).findByTestId('shared-mermaid-renderer')
    expectDisabledRunReason(dialog, 'Run this bundled showcase from the CLI.')
  })

  it('fails closed without crashing when an older backend omits detail run support', async () => {
    currentDetail = detail({ run_support: undefined as never })
    renderView()
    const dialog = await openView()

    await within(dialog).findByTestId('shared-mermaid-renderer')
    expectDisabledRunReason(
      dialog,
      workflowCopy.workflowRunSupportUnavailable
    )
  })

  it('keeps Run disabled for an unknown backend run-support reason', async () => {
    currentDetail = detail({
      compatibility: { findings: [], level: 'unsupported', runnable: false },
      run_support: { reason: 'future_backend_rule' as never, supported: false },
      trust_state: 'trusted'
    })
    renderView()
    const dialog = await openView()

    await within(dialog).findByTestId('shared-mermaid-renderer')
    expectDisabledRunReason(
      dialog,
      workflowCopy.workflowRunSupportUnavailable
    )
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
      apiStructured.mock.calls.filter(([request]) =>
        request.path.startsWith(`/api/plugins/workflow/workflows/${encodeURIComponent(WORKFLOW_NAME)}`)
      )
    ).toHaveLength(1)
  })

  it('replaces View with Review & Run for the same captured profile without stacking dialogs or refetching', async () => {
    currentCatalogDefinition = definition({
      inputs: [
        { max_bytes: 12, name: 'symptom', required: true, type: 'text' },
        { name: 'evidence', required: true, type: 'file' }
      ],
      source: 'showcase',
      supported_inputs: { reason: 'flat_inputs', supported: true },
      trust_state: 'verified_bundled'
    })
    currentDetail = detail({ ...currentCatalogDefinition })
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
      apiStructured.mock.calls.filter(([request]) =>
        request.path.startsWith(`/api/plugins/workflow/workflows/${encodeURIComponent(WORKFLOW_NAME)}`)
      )
    ).toHaveLength(1)
    const symptom = within(review).getByRole('textbox', { name: 'symptom' })
    expect(symptom.tagName).toBe('TEXTAREA')
    const fixture = within(review).getByRole('group', { name: 'evidence' })
    expect(within(fixture).getByText('Bundled fixture')).toBeTruthy()
    expect(fixture.querySelector('input, textarea, button, [contenteditable="true"]')).toBeNull()
    fireEvent.change(symptom, { target: { value: 'fan noise' } })
    fireEvent.click(within(review).getByRole('button', { name: 'Start workflow' }))
    await waitFor(() => {
      const request = apiStructured.mock.calls.find(
        ([candidate]) => candidate.path === '/api/plugins/workflow/runs'
      )?.[0]

      expect(request).toMatchObject({
        body: { values: { symptom: 'fan noise' } },
        path: '/api/plugins/workflow/runs',
        profile: 'profile-a'
      })
      expect(request?.body?.values).not.toHaveProperty('arguments')
      expect(request?.body?.values).not.toHaveProperty('evidence')
    })
  })
})
