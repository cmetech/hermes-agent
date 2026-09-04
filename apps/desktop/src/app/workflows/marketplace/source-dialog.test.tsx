// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { WorkflowMarketplaceApiError } from '@/api/workflow-marketplace'
import { I18nProvider } from '@/i18n'
import type { WorkflowMarketplaceSourceRecord } from '@/types/hermes'

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
const NOW = '2026-09-04T00:00:00Z'
const COMMIT = 'c'.repeat(40)
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
    errors: {},
    isReconciling: false,
    operationForSource: vi.fn(),
    operations: [],
    reconcile: vi.fn(),
    retry: vi.fn().mockResolvedValue(null),
    start: vi.fn().mockResolvedValue(null),
    ...overrides
  }
}

function renderDialog(controller = operations(), supported = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  return {
    client,
    controller,
    ...render(
      <QueryClientProvider client={client}>
        <I18nProvider>
          <ManageWorkflowSourcesDialog
            onClose={vi.fn()}
            open
            operations={controller}
            scope={scope}
            supported={supported}
          />
        </I18nProvider>
      </QueryClientProvider>
    )
  }
}

describe('ManageWorkflowSourcesDialog', () => {
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
        })
      ]
    })
    renderDialog()

    expect((await screen.findAllByText('Default branch')).length).toBe(5)
    expect(screen.getAllByText(COMMIT).length).toBe(2)
    expect(screen.getAllByText(/Last verified/).length).toBe(2)
    expect(screen.getAllByText('Disabled').length).toBeGreaterThan(0)
    expect(screen.getByText('Authentication required')).toBeTruthy()
    expect(screen.getByText(/Git credential helper, SSH agent\/config, gh auth/)).toBeTruthy()
    expect(screen.getByText('Stale source')).toBeTruthy()
    expect(screen.getByText('Never refreshed')).toBeTruthy()
    expect(screen.getByText('Unavailable')).toBeTruthy()
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

  it('requires remove confirmation and explains installed packages remain', async () => {
    renderDialog()
    const row = await screen.findByRole('article', { name: 'company source' })
    fireEvent.click(within(row).getByRole('button', { name: 'Remove company' }))
    expect(screen.getByText(/Installed packages remain installed/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Remove source' }))
    await waitFor(() => expect(api.remove).toHaveBeenCalledWith('company', scope))
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
    fireEvent.click(await screen.findByRole('button', { name: 'Refresh all' }))
    await waitFor(() => expect(order).toEqual(['company', 'team']))
    expect(screen.getByText('Refresh status unavailable')).toBeTruthy()
    expect(controller.start).not.toHaveBeenCalledWith('off', expect.anything())
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
