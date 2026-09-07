// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { StrictMode } from 'react'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { WorkflowMarketplaceApiError } from '@/api/workflow-marketplace'
import { I18nProvider } from '@/i18n'
import type { WorkflowMarketplaceOperation, WorkflowMarketplaceSourceRecord } from '@/types/hermes'

import { marketplaceKeys } from './query-keys'
import { ManageWorkflowSourcesDialog } from './source-dialog'
import type { MarketplaceOperationController } from './use-marketplace-operation'

const api = vi.hoisted(() => ({ add: vi.fn(), enable: vi.fn(), list: vi.fn(), remove: vi.fn(), update: vi.fn() }))

vi.mock('@/hermes', () => ({
  addWorkflowMarketplaceSource: (...args: unknown[]) => api.add(...args),
  listWorkflowMarketplaceSources: (...args: unknown[]) => api.list(...args),
  refreshWorkflowMarketplaceSource: vi.fn(),
  removeWorkflowMarketplaceSource: (...args: unknown[]) => api.remove(...args),
  setWorkflowMarketplaceSourceEnabled: (...args: unknown[]) => api.enable(...args),
  updateWorkflowMarketplaceSource: (...args: unknown[]) => api.update(...args)
}))

const scope = { connectionId: 'remote-a', profile: 'support' }
const scopeB = { connectionId: 'remote-b', profile: 'support' }
const NOW = '2026-09-04T00:00:00Z'
const COMMIT = 'c'.repeat(40)
const OPERATION_ID = `wmop_${'a'.repeat(12)}_${'b'.repeat(32)}`
const originalHasPointerCapture = Element.prototype.hasPointerCapture
const originalReleasePointerCapture = Element.prototype.releasePointerCapture
const originalScrollIntoView = Element.prototype.scrollIntoView

beforeAll(() => {
  Element.prototype.hasPointerCapture = vi.fn(() => false)
  Element.prototype.releasePointerCapture = vi.fn()
  Element.prototype.scrollIntoView = vi.fn()
})

afterAll(() => {
  Element.prototype.hasPointerCapture = originalHasPointerCapture
  Element.prototype.releasePointerCapture = originalReleasePointerCapture
  Element.prototype.scrollIntoView = originalScrollIntoView
})

function source(overrides: Partial<WorkflowMarketplaceSourceRecord> = {}): WorkflowMarketplaceSourceRecord {
  return {
    attempted_at: NOW,
    diagnostic_code: null,
    enabled: true,
    message: null,
    name: 'company',
    ref: null,
    refresh_state: 'fresh',
    repository_url: 'https://example.test/company/workflows.git',
    resolved_commit: COMMIT,
    verified_at: NOW,
    verified_package_count: 2,
    ...overrides
  }
}

function operations(overrides: Partial<MarketplaceOperationController> = {}): MarketplaceOperationController {
  return {
    cancel: vi.fn().mockResolvedValue(null),
    captureOrigin: vi.fn(() => ({ generation: 1, scopeKey: 'remote-a::support' })),
    errors: {},
    isReconciling: false,
    operationForSource: vi.fn(),
    operations: [],
    originIsCurrent: vi.fn(() => true),
    reconcile: vi.fn(),
    retry: vi.fn().mockResolvedValue(null),
    start: vi.fn().mockResolvedValue(null),
    ...overrides
  }
}

function runningOperation(): WorkflowMarketplaceOperation {
  return {
    created_at: NOW,
    error: null,
    finished_at: null,
    id: OPERATION_ID,
    kind: 'refresh',
    phase: 'fetching',
    profile: 'support',
    progress: 40,
    result: null,
    schema_version: 1,
    source_name: 'company',
    started_at: NOW,
    state: 'running',
    updated_at: NOW
  }
}

function cancelledOperation(): WorkflowMarketplaceOperation {
  return {
    ...runningOperation(),
    error: null,
    finished_at: NOW,
    phase: 'cancelled',
    progress: 40,
    result: null,
    state: 'cancelled'
  }
}

function deferred<T>() {
  let reject!: (reason?: unknown) => void
  let resolve!: (value: T) => void

  const promise = new Promise<T>((onResolve, onReject) => {
    reject = onReject
    resolve = onResolve
  })

  return { promise, reject, resolve }
}

function renderDialog(controller = operations(), supported = true, requestedScope = scope, locale = 'en') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return {
    client,
    controller,
    ...render(
      <QueryClientProvider client={client}>
        <I18nProvider configClient={null} initialLocale={locale}>
          <ManageWorkflowSourcesDialog
            onClose={vi.fn()}
            open
            operations={controller}
            scope={requestedScope}
            supported={supported}
          />
        </I18nProvider>
      </QueryClientProvider>
    )
  }
}

function rerenderDialog(
  view: ReturnType<typeof renderDialog>,
  controller: MarketplaceOperationController,
  requestedScope = scopeB
) {
  view.rerender(
    <QueryClientProvider client={view.client}>
      <I18nProvider>
        <ManageWorkflowSourcesDialog onClose={vi.fn()} open operations={controller} scope={requestedScope} supported />
      </I18nProvider>
    </QueryClientProvider>
  )
}

describe('ManageWorkflowSourcesDialog', () => {
  it('localizes source progress without altering the reported phase or percentage', async () => {
    const active = runningOperation()
    renderDialog(operations({ operationForSource: () => active }), true, scope, 'ja')
    const progress = await screen.findByText(/fetching.*40/)
    expect(progress.textContent).toMatch(/[\u3040-\u30ff\u3400-\u9fff]/)
  })

  beforeEach(() => {
    api.list.mockResolvedValue({ profile: 'support', sources: [source()] })
    api.add.mockResolvedValue({ profile: 'support', source: source() })
    api.update.mockResolvedValue({ profile: 'support', source: source() })
    api.enable.mockResolvedValue({ profile: 'support', source: source() })
    api.remove.mockResolvedValue({ profile: 'support', source: source() })
  })

  afterEach(() => {
    cleanup()
    vi.clearAllMocks()
  })

  it('shows backend freshness, default ref, commit, verification, and disabled/failure states', async () => {
    api.list.mockResolvedValue({
      profile: 'support',
      sources: [
        source(),
        source({
          diagnostic_code: 'source_authentication_failed',
          enabled: false,
          name: 'private',
          refresh_state: 'authentication-failed',
          repository_url: 'git@example.test:team/repo.git',
          resolved_commit: null,
          verified_at: null,
          verified_package_count: 0
        }),
        source({
          diagnostic_code: 'network_unavailable',
          message: 'refresh failed',
          name: 'stale',
          refresh_state: 'stale'
        }),
        source({
          attempted_at: null,
          name: 'new',
          refresh_state: null,
          resolved_commit: null,
          verified_at: null,
          verified_package_count: 0
        }),
        source({
          diagnostic_code: 'network_unavailable',
          message: 'refresh failed',
          name: 'offline',
          refresh_state: 'unavailable',
          resolved_commit: null,
          verified_at: null,
          verified_package_count: 0
        }),
        source({
          diagnostic_code: 'source_malformed',
          message: 'refresh failed',
          name: 'malformed',
          refresh_state: 'malformed',
          resolved_commit: null,
          verified_at: null,
          verified_package_count: 0
        }),
        source({
          diagnostic_code: 'source_incompatible',
          message: 'refresh failed',
          name: 'incompatible',
          refresh_state: 'incompatible',
          resolved_commit: null,
          verified_at: null,
          verified_package_count: 0
        })
      ]
    })
    renderDialog()

    expect((await screen.findAllByText('Default branch')).length).toBe(7)
    expect(screen.getAllByText(COMMIT).length).toBe(2)
    expect(screen.getAllByText(/Last verified/).length).toBe(2)
    expect(screen.getAllByText('Disabled').length).toBeGreaterThan(0)
    expect(screen.getAllByText('Authentication required').length).toBeGreaterThan(0)
    expect(screen.getByText(/Git credential helper, SSH agent\/config, gh auth/)).toBeTruthy()
    expect(screen.getByText('Stale source')).toBeTruthy()
    expect(screen.getByText('Never refreshed')).toBeTruthy()
    expect(screen.getAllByText('Unavailable').length).toBeGreaterThan(0)
    expect(screen.getByText('Malformed')).toBeTruthy()
    expect(screen.getByText('Incompatible')).toBeTruthy()
    const disabledAuth = screen.getByRole('article', { name: 'private source' })
    expect(within(disabledAuth).getByText('Disabled')).toBeTruthy()
    expect(within(disabledAuth).getAllByText('Authentication required').length).toBeGreaterThan(0)
  })

  it.each([
    ['public', 'https://example.test/public/workflows.git'],
    ['private', 'git@example.test:team/repo.git']
  ])('adds the %s source without collecting credentials', async (name, repositoryUrl) => {
    renderDialog()
    fireEvent.click(await screen.findByRole('button', { name: 'Add source' }))
    fireEvent.change(screen.getByLabelText('Source name'), { target: { value: name } })
    fireEvent.change(screen.getByLabelText('Repository URL'), { target: { value: repositoryUrl } })
    fireEvent.click(screen.getByRole('button', { name: 'Save source' }))

    await waitFor(() => expect(api.add).toHaveBeenCalledWith({ enabled: true, name, ref: null, repositoryUrl }, scope))
    expect(screen.queryByLabelText(/token|password|private key/i)).toBeNull()
  })

  it('edits configuration, toggles enabled state, and preserves source identity', async () => {
    renderDialog()
    const row = await screen.findByRole('article', { name: 'company source' })
    fireEvent.click(within(row).getByRole('button', { name: 'Edit company' }))
    expect((screen.getByLabelText('Source name') as HTMLInputElement).disabled).toBe(true)
    fireEvent.change(screen.getByLabelText('Repository URL'), {
      target: { value: 'https://example.test/company/new.git' }
    })
    fireEvent.change(screen.getByLabelText('Git ref (optional)'), { target: { value: 'stable' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save source' }))
    await waitFor(() =>
      expect(api.update).toHaveBeenCalledWith(
        'company',
        { enabled: true, ref: 'stable', repositoryUrl: 'https://example.test/company/new.git' },
        scope
      )
    )

    fireEvent.click(within(row).getByRole('button', { name: 'Disable company' }))
    await waitFor(() => expect(api.enable).toHaveBeenCalledWith('company', false, scope))
  })

  it('uses matching Enable and Disable action labels for source toggles', async () => {
    api.list.mockResolvedValue({
      profile: 'support',
      sources: [source(), source({ enabled: false, name: 'disabled-source' })]
    })
    renderDialog()

    const enabled = await screen.findByRole('article', { name: 'company source' })
    const disabled = screen.getByRole('article', { name: 'disabled-source source' })
    expect(within(enabled).getByRole('button', { name: 'Disable company' }).textContent).toBe('Disable')
    expect(within(disabled).getByRole('button', { name: 'Enable disabled-source' }).textContent).toBe('Enable')
  })

  it.each([
    ['company', 'team', false],
    ['team', 'company', true]
  ] as const)(
    'serializes rapid %s then %s mutations regardless of deferred completion order',
    async (firstName, secondName, resolveBlockedFirst) => {
      api.list.mockResolvedValue({ profile: 'support', sources: [source(), source({ name: 'team' })] })
      const company = deferred<{ profile: string; source: WorkflowMarketplaceSourceRecord }>()
      const team = deferred<{ profile: string; source: WorkflowMarketplaceSourceRecord }>()
      api.enable.mockImplementation(name => (name === 'company' ? company.promise : team.promise))
      renderDialog()

      const first = await screen.findByRole('article', { name: `${firstName} source` })
      const second = screen.getByRole('article', { name: `${secondName} source` })
      act(() => {
        fireEvent.click(within(first).getByRole('button', { name: `Disable ${firstName}` }))
        fireEvent.click(within(second).getByRole('button', { name: `Disable ${secondName}` }))
      })

      expect(api.enable).toHaveBeenCalledTimes(1)
      expect(api.enable).toHaveBeenCalledWith(firstName, false, scope)
      expect((screen.getByRole('button', { name: 'Add source' }) as HTMLButtonElement).disabled).toBe(true)
      expect((within(second).getByRole('button', { name: `Edit ${secondName}` }) as HTMLButtonElement).disabled).toBe(
        true
      )
      expect((within(second).getByRole('button', { name: `Remove ${secondName}` }) as HTMLButtonElement).disabled).toBe(
        true
      )

      if (resolveBlockedFirst) {
        ;(secondName === 'company' ? company : team).resolve({ profile: 'support', source: source() })
        await Promise.resolve()
      }

      ;(firstName === 'company' ? company : team).resolve({ profile: 'support', source: source() })
      await waitFor(() =>
        expect(
          (within(second).getByRole('button', { name: `Disable ${secondName}` }) as HTMLButtonElement).disabled
        ).toBe(false)
      )
      expect(api.enable).toHaveBeenCalledTimes(1)
    }
  )

  it('preserves a failed mutation while another succeeds and Retry remains exact', async () => {
    api.list.mockResolvedValue({ profile: 'support', sources: [source(), source({ name: 'team' })] })
    api.enable.mockImplementationOnce(() => Promise.reject(new Error('network')))
    renderDialog()

    const company = await screen.findByRole('article', { name: 'company source' })
    const team = screen.getByRole('article', { name: 'team source' })
    fireEvent.click(within(company).getByRole('button', { name: 'Disable company' }))
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeTruthy()

    fireEvent.click(within(team).getByRole('button', { name: 'Disable team' }))
    await waitFor(() => expect(api.enable).toHaveBeenCalledWith('team', false, scope))
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))

    await waitFor(() => expect(api.enable).toHaveBeenCalledTimes(3))
    expect(api.enable.mock.calls).toEqual([
      ['company', false, scope],
      ['team', false, scope],
      ['company', false, scope]
    ])
  })

  it('keeps source mutations functional and single-admission under StrictMode replay', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const controller = operations()

    const view = render(
      <StrictMode>
        <QueryClientProvider client={client}>
          <I18nProvider>
            <ManageWorkflowSourcesDialog onClose={vi.fn()} open operations={controller} scope={scope} supported />
          </I18nProvider>
        </QueryClientProvider>
      </StrictMode>
    )

    fireEvent.click(await screen.findByRole('button', { name: 'Add source' }))
    fireEvent.change(screen.getByLabelText('Source name'), { target: { value: 'team' } })
    fireEvent.change(screen.getByLabelText('Repository URL'), {
      target: { value: 'https://example.test/team.git' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save source' }))
    await waitFor(() => expect(api.add).toHaveBeenCalledTimes(1))

    const row = await screen.findByRole('article', { name: 'company source' })
    fireEvent.click(within(row).getByRole('button', { name: 'Edit company' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save source' }))
    await waitFor(() => expect(api.update).toHaveBeenCalledTimes(1))

    fireEvent.click(within(row).getByRole('button', { name: 'Disable company' }))
    await waitFor(() => expect(api.enable).toHaveBeenCalledTimes(1))

    fireEvent.click(within(row).getByRole('button', { name: 'Remove company' }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove source' }))
    await waitFor(() => expect(api.remove).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Remove source' })).toBeNull())

    view.unmount()
    expect(api.add).toHaveBeenCalledTimes(1)
    expect(api.update).toHaveBeenCalledTimes(1)
    expect(api.enable).toHaveBeenCalledTimes(1)
    expect(api.remove).toHaveBeenCalledTimes(1)
  })

  it('keeps removal confirmation open when the exact mutation fails', async () => {
    api.remove.mockRejectedValueOnce(new Error('network'))
    renderDialog()
    const row = await screen.findByRole('article', { name: 'company source' })
    fireEvent.click(within(row).getByRole('button', { name: 'Remove company' }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove source' }))

    await waitFor(() => expect(api.remove).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('button', { name: 'Remove source' })).toBeTruthy()
  })

  it('keeps removal busy through backend invalidation and ignores repeated confirmation', async () => {
    const pending = deferred<{ profile: string; source: WorkflowMarketplaceSourceRecord }>()
    api.remove.mockReturnValueOnce(pending.promise)
    const view = renderDialog()
    const invalidate = vi.spyOn(view.client, 'invalidateQueries')
    const row = await screen.findByRole('article', { name: 'company source' })
    fireEvent.click(within(row).getByRole('button', { name: 'Remove company' }))

    const confirm = screen.getByRole('button', { name: 'Remove source' })
    const confirmation = screen.getByRole('dialog')
    act(() => {
      fireEvent.click(confirm)
      fireEvent.keyDown(confirmation, { key: 'Enter' })
      fireEvent.click(confirm)
    })

    expect(api.remove).toHaveBeenCalledTimes(1)
    expect((screen.getByRole('button', { name: 'Removing source…' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: 'Cancel' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.queryByRole('alert')).toBeNull()

    await act(async () => pending.resolve({ profile: 'support', source: source() }))
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({ queryKey: marketplaceKeys.sources('remote-a::support') })
    )
    expect(screen.getByRole('button', { name: 'Source removed' })).toBeTruthy()
    expect(screen.queryByRole('alert')).toBeNull()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Source removed' })).toBeNull())
    expect(api.remove).toHaveBeenCalledTimes(1)
  })

  it('requires remove confirmation and explains installed packages remain', async () => {
    renderDialog()
    const row = await screen.findByRole('article', { name: 'company source' })
    fireEvent.click(within(row).getByRole('button', { name: 'Remove company' }))
    expect(screen.getByText(/Installed packages remain installed/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Remove source' }))
    await waitFor(() => expect(api.remove).toHaveBeenCalledWith('company', scope))
  })

  it('invalidates a stale remove confirmation before it can target a new scope', async () => {
    const controller = operations()
    const view = renderDialog(controller)
    const row = await screen.findByRole('article', { name: 'company source' })
    fireEvent.click(within(row).getByRole('button', { name: 'Remove company' }))
    expect(screen.getByRole('button', { name: 'Remove source' })).toBeTruthy()

    rerenderDialog(view, controller)

    expect(screen.queryByRole('button', { name: 'Remove source' })).toBeNull()
    expect(api.remove).not.toHaveBeenCalled()
  })

  it('refreshes one or all enabled sources sequentially while preserving each failure', async () => {
    api.list.mockResolvedValue({
      profile: 'support',
      sources: [source(), source({ enabled: true, name: 'team' }), source({ enabled: false, name: 'off' })]
    })
    const order: string[] = []

    const controller = operations({
      errors: { company: 'status' },
      start: vi.fn(async name => {
        order.push(name)

        return null
      })
    })

    renderDialog(controller)
    const refreshAll = await screen.findByRole('button', { name: 'Refresh all' })
    await waitFor(() => expect((refreshAll as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(refreshAll)
    await waitFor(() => expect(order).toEqual(['company', 'team']))
    expect(screen.getByText('Refresh status unavailable')).toBeTruthy()
    expect(controller.start).not.toHaveBeenCalledWith('off', expect.anything())
  })

  it('stops an old-scope dialog Refresh all and admits an independent new-scope sequence', async () => {
    api.list.mockResolvedValue({
      profile: 'support',
      sources: [source(), source({ name: 'team' })]
    })
    const first = deferred<null>()
    let activeScopeKey = 'remote-a::support'
    const starts: Array<{ name: string; scopeKey: string }> = []

    const controller = operations({
      captureOrigin: vi.fn(() => ({ generation: 1, scopeKey: activeScopeKey })),
      originIsCurrent: vi.fn(origin => origin.scopeKey === activeScopeKey),
      start: vi.fn(async (name, origin) => {
        starts.push({ name, scopeKey: origin?.scopeKey ?? '' })

        if (origin?.scopeKey === 'remote-a::support' && name === 'company') {
          return first.promise
        }

        return null
      })
    })

    const view = renderDialog(controller)

    const refreshAll = await screen.findByRole('button', { name: 'Refresh all' })
    await waitFor(() => expect((refreshAll as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(refreshAll)
    await waitFor(() => expect(starts).toEqual([{ name: 'company', scopeKey: 'remote-a::support' }]))

    activeScopeKey = 'remote-b::support'
    rerenderDialog(view, controller)
    const newScopeRefresh = await screen.findByRole('button', { name: 'Refresh all' })
    expect(newScopeRefresh.getAttribute('aria-busy')).toBe('false')
    await waitFor(() => expect((newScopeRefresh as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(newScopeRefresh)
    await waitFor(() => expect(starts).toContainEqual({ name: 'company', scopeKey: 'remote-b::support' }))

    await act(async () => first.resolve(null))
    expect(starts).not.toContainEqual({ name: 'team', scopeKey: 'remote-a::support' })
    expect(starts).toContainEqual({ name: 'team', scopeKey: 'remote-b::support' })
  })

  it('renders a cancelled terminal operation as Cancelled instead of Unavailable', async () => {
    const cancelled = cancelledOperation()
    const controller = operations({ operationForSource: vi.fn(() => cancelled), operations: [cancelled] })
    renderDialog(controller)

    const row = await screen.findByRole('article', { name: 'company source' })
    expect(within(row).getByText('Cancelled')).toBeTruthy()
    expect(within(row).queryByText('Unavailable')).toBeNull()
  })

  it('renders bounded generic failures and Retry repeats only the exact failed action', async () => {
    api.enable.mockRejectedValueOnce(
      new WorkflowMarketplaceApiError('source_authentication_failed', 401, 'token=raw /private/tmp/repo')
    )
    const controller = operations()
    renderDialog(controller)
    const row = await screen.findByRole('article', { name: 'company source' })
    fireEvent.click(within(row).getByRole('button', { name: 'Disable company' }))
    expect(await screen.findByText('Source authentication required')).toBeTruthy()
    expect(screen.queryByText(/token=raw|private\/tmp/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(api.enable).toHaveBeenCalledTimes(2))
    expect(api.remove).not.toHaveBeenCalled()
  })

  it('drops stale action Retry and draft state when the exact scope changes', async () => {
    api.enable.mockRejectedValueOnce(new Error('network'))
    const controller = operations()
    const view = renderDialog(controller)
    const row = await screen.findByRole('article', { name: 'company source' })
    fireEvent.click(within(row).getByRole('button', { name: 'Disable company' }))
    expect(await screen.findByRole('button', { name: 'Retry' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Add source' }))
    fireEvent.change(screen.getByLabelText('Source name'), { target: { value: 'profile-a-only' } })
    rerenderDialog(view, controller)

    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull()
    expect(screen.queryByLabelText('Source name')).toBeNull()
    expect(api.enable).toHaveBeenCalledTimes(1)
  })

  it('retries a transient operation status read without admitting a new refresh', async () => {
    const active = runningOperation()

    const controller = operations({
      errors: { company: 'status' },
      operationForSource: vi.fn(() => active),
      retry: vi.fn().mockResolvedValue(active)
    })

    renderDialog(controller)

    const row = await screen.findByRole('article', { name: 'company source' })
    fireEvent.click(within(row).getByRole('button', { name: 'Retry' }))

    await waitFor(() => expect(controller.retry).toHaveBeenCalledWith(OPERATION_ID))
    expect(controller.start).not.toHaveBeenCalled()
  })

  it('starts exactly one fresh refresh after an operation was evicted', async () => {
    const controller = operations({ errors: { company: 'evicted' } })
    renderDialog(controller)

    const row = await screen.findByRole('article', { name: 'company source' })
    fireEvent.click(within(row).getByRole('button', { name: 'Retry' }))

    await waitFor(() => expect(controller.start).toHaveBeenCalledTimes(1))
    expect(controller.start).toHaveBeenCalledWith('company')
    expect(controller.retry).not.toHaveBeenCalled()
  })

  it('does not let a late old-scope save close or replace a new-scope draft', async () => {
    const pending = deferred<{ profile: string; source: WorkflowMarketplaceSourceRecord }>()
    api.update.mockReturnValueOnce(pending.promise)
    const controller = operations()
    const view = renderDialog(controller)
    const row = await screen.findByRole('article', { name: 'company source' })
    fireEvent.click(within(row).getByRole('button', { name: 'Edit company' }))
    fireEvent.change(screen.getByLabelText('Repository URL'), {
      target: { value: 'https://example.test/company/old-scope.git' }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save source' }))
    await waitFor(() => expect(api.update).toHaveBeenCalledTimes(1))

    const invalidate = vi.spyOn(view.client, 'invalidateQueries')
    rerenderDialog(view, controller)
    fireEvent.click(await screen.findByRole('button', { name: 'Add source' }))
    fireEvent.change(screen.getByLabelText('Source name'), { target: { value: 'new-scope-draft' } })

    await act(async () => pending.resolve({ profile: 'support', source: source() }))
    expect((screen.getByLabelText('Source name') as HTMLInputElement).value).toBe('new-scope-draft')
    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: marketplaceKeys.sources('remote-b::support') })
  })

  it.each(['profile-a-first', 'profile-b-first'] as const)(
    'keeps mutations in an origin-scoped ledger across A to B to A when %s completes',
    async completionOrder => {
      const pendingA = deferred<{ profile: string; source: WorkflowMarketplaceSourceRecord }>()
      const pendingB = deferred<{ profile: string; source: WorkflowMarketplaceSourceRecord }>()
      api.enable.mockImplementation((_name: string, _enabled: boolean, requestedScope: typeof scope) =>
        requestedScope.connectionId === scope.connectionId ? pendingA.promise : pendingB.promise
      )
      const controller = operations()
      const view = renderDialog(controller)
      const invalidate = vi.spyOn(view.client, 'invalidateQueries')

      let row = await screen.findByRole('article', { name: 'company source' })
      fireEvent.click(within(row).getByRole('button', { name: 'Disable company' }))
      expect(api.enable).toHaveBeenCalledWith('company', false, scope)

      rerenderDialog(view, controller, scopeB)
      row = await screen.findByRole('article', { name: 'company source' })
      fireEvent.click(within(row).getByRole('button', { name: 'Disable company' }))
      expect(api.enable).toHaveBeenCalledWith('company', false, scopeB)
      expect(api.enable).toHaveBeenCalledTimes(2)

      rerenderDialog(view, controller, scope)
      row = await screen.findByRole('article', { name: 'company source' })
      const returnedAction = within(row).getByRole('button', { name: 'Disable company' }) as HTMLButtonElement
      expect(returnedAction.disabled).toBe(true)
      fireEvent.click(returnedAction)
      expect(api.enable).toHaveBeenCalledTimes(2)

      await waitFor(
        () =>
          expect(api.list.mock.calls.filter(([requestedScope]) => requestedScope === scope).length).toBeGreaterThan(1),
        { timeout: 2_000 }
      )
      const aReadsBeforeCompletion = api.list.mock.calls.filter(([requestedScope]) => requestedScope === scope).length

      const resolveA = async () => {
        const before = invalidate.mock.calls.length
        await act(async () => pendingA.resolve({ profile: 'support', source: source({ enabled: false }) }))
        await waitFor(() => expect(invalidate.mock.calls.length).toBe(before + 2))
        expect(invalidate.mock.calls.slice(before).map(([filters]) => filters?.queryKey)).toEqual(
          expect.arrayContaining([
            marketplaceKeys.sources('remote-a::support'),
            marketplaceKeys.searchRoot('remote-a::support')
          ])
        )
        await waitFor(() =>
          expect(api.list.mock.calls.filter(([requestedScope]) => requestedScope === scope).length).toBeGreaterThan(
            aReadsBeforeCompletion
          )
        )
      }

      const resolveB = async () => {
        const before = invalidate.mock.calls.length
        await act(async () => pendingB.resolve({ profile: 'support', source: source({ enabled: false }) }))
        await waitFor(() => expect(invalidate.mock.calls.length).toBe(before + 2))
        expect(invalidate.mock.calls.slice(before).map(([filters]) => filters?.queryKey)).toEqual(
          expect.arrayContaining([
            marketplaceKeys.sources('remote-b::support'),
            marketplaceKeys.searchRoot('remote-b::support')
          ])
        )
      }

      if (completionOrder === 'profile-a-first') {
        await resolveA()
        await resolveB()
      } else {
        await resolveB()
        await resolveA()
      }

      expect(api.enable.mock.calls).toEqual([
        ['company', false, scope],
        ['company', false, scopeB]
      ])
      expect(screen.queryByRole('alert')).toBeNull()
    }
  )

  it('reconciles operations when the dialog is reopened', async () => {
    const controller = operations()
    const view = renderDialog(controller)

    view.rerender(
      <QueryClientProvider client={view.client}>
        <I18nProvider>
          <ManageWorkflowSourcesDialog onClose={vi.fn()} open={false} operations={controller} scope={scope} supported />
        </I18nProvider>
      </QueryClientProvider>
    )
    view.rerender(
      <QueryClientProvider client={view.client}>
        <I18nProvider>
          <ManageWorkflowSourcesDialog onClose={vi.fn()} open operations={controller} scope={scope} supported />
        </I18nProvider>
      </QueryClientProvider>
    )

    await waitFor(() => expect(controller.reconcile).toHaveBeenCalledTimes(1))
  })

  it('contains source-list failures and Retry repeats only that safe read', async () => {
    api.list
      .mockRejectedValueOnce(new Error('token=raw /private/tmp/repo'))
      .mockResolvedValueOnce({ profile: 'support', sources: [source()] })
    renderDialog()

    expect(
      await screen.findByText('The source action could not be completed. Check the source configuration and try again.')
    ).toBeTruthy()
    expect(screen.queryByText(/token=raw|private\/tmp/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))

    await waitFor(() => expect(api.list).toHaveBeenCalledTimes(2))
    expect(await screen.findByRole('article', { name: 'company source' })).toBeTruthy()
    expect(api.add).not.toHaveBeenCalled()
    expect(api.update).not.toHaveBeenCalled()
    expect(api.enable).not.toHaveBeenCalled()
    expect(api.remove).not.toHaveBeenCalled()
  })

  it('shows upgrade guidance instead of source controls for an unsupported backend', async () => {
    renderDialog(operations(), false)
    expect(await screen.findByText(/Upgrade Hermes/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Add source' })).toBeNull()
  })
})
