// @vitest-environment jsdom
import { QueryClient, QueryClientProvider, type QueryKey } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { profileScopeKey } from '@/api/client'
import { WorkflowMarketplaceApiError } from '@/api/workflow-marketplace'
import type * as Hermes from '@/hermes'
import { I18nProvider } from '@/i18n'
import type {
  WorkflowMarketplaceCatalogPackage,
  WorkflowMarketplaceInstalledPackage,
  WorkflowMarketplaceInstallReview,
  WorkflowMarketplaceOperation,
  WorkflowMarketplaceOperationKind,
  WorkflowMarketplaceOperationResult,
  WorkflowMarketplacePackageDetail,
  WorkflowMarketplaceRemoveReview,
  WorkflowMarketplaceTrustReview,
  WorkflowMarketplaceUpdateReview
} from '@/types/hermes'

import { InstalledPackages } from './installed-packages'
import {
  createLifecycleHarness,
  legacyInspectionFixture,
  lifecycleIdentity,
  renderLifecycleHarness
} from './lifecycle-test-harness'
import { MarketplacePackageDetail } from './package-detail'
import { MarketplacePackageList } from './package-list'
import { marketplaceKeys } from './query-keys'
import { useMarketplaceReadOnlyScope } from './supervisor-provider'

import { WorkflowMarketplaceView } from './index'

const api = vi.hoisted(() => ({
  cancelOperation: vi.fn(),
  capabilities: vi.fn(),
  checkUpdates: vi.fn(),
  confirmInstall: vi.fn(),
  confirmRemove: vi.fn(),
  confirmUpdate: vi.fn(),
  getOperation: vi.fn(),
  grantTrust: vi.fn(),
  inspect: vi.fn(),
  installed: vi.fn(),
  listOperations: vi.fn(),
  prepareInstall: vi.fn(),
  prepareRemove: vi.fn(),
  prepareUpdate: vi.fn(),
  refreshSource: vi.fn(),
  reviewTrust: vi.fn(),
  search: vi.fn(),
  sources: vi.fn()
}))

vi.mock('@/hermes', async importOriginal => ({
  ...(await importOriginal<typeof Hermes>()),
  cancelWorkflowMarketplaceOperation: (...args: unknown[]) => api.cancelOperation(...args),
  checkWorkflowPackageUpdates: (...args: unknown[]) => api.checkUpdates(...args),
  confirmWorkflowPackageInstall: (...args: unknown[]) => api.confirmInstall(...args),
  confirmWorkflowPackageRemoval: (...args: unknown[]) => api.confirmRemove(...args),
  confirmWorkflowPackageUpdate: (...args: unknown[]) => api.confirmUpdate(...args),
  getWorkflowMarketplaceCapabilities: (...args: unknown[]) => api.capabilities(...args),
  getWorkflowMarketplaceOperation: (...args: unknown[]) => api.getOperation(...args),
  grantWorkflowPackageTrust: (...args: unknown[]) => api.grantTrust(...args),
  inspectWorkflowPackage: (...args: unknown[]) => api.inspect(...args),
  isWorkflowMarketplaceUnsupportedError: (error: unknown) =>
    typeof error === 'object' && error !== null && 'code' in error && error.code === 'marketplace_unsupported',
  listInstalledWorkflowPackages: (...args: unknown[]) => api.installed(...args),
  listWorkflowMarketplaceOperations: (...args: unknown[]) => api.listOperations(...args),
  listWorkflowMarketplaceSources: (...args: unknown[]) => api.sources(...args),
  prepareWorkflowPackageInstall: (...args: unknown[]) => api.prepareInstall(...args),
  prepareWorkflowPackageRemoval: (...args: unknown[]) => api.prepareRemove(...args),
  prepareWorkflowPackageUpdate: (...args: unknown[]) => api.prepareUpdate(...args),
  refreshWorkflowMarketplaceSource: (...args: unknown[]) => api.refreshSource(...args),
  reviewWorkflowPackageTrust: (...args: unknown[]) => api.reviewTrust(...args),
  searchWorkflowPackages: (...args: unknown[]) => api.search(...args)
}))

const NOW = '2026-09-04T00:00:00Z'
const DIGEST = 'a'.repeat(64)
const COMMIT = 'b'.repeat(40)
const OPERATION_ID = `wmop_${'c'.repeat(12)}_${'d'.repeat(32)}`
const INSTALL_TOKEN = 'install-confirmation-token-value-1234567890'
const UPDATE_TOKEN = 'update-confirmation-token-value-1234567890'
const REMOVE_TOKEN = 'remove-confirmation-token-value-1234567890'
const TRUST_TOKEN = 'trust-confirmation-token-value-1234567890'
const scopeA = { connectionId: 'remote-a', profile: 'support' }
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

function packageItem(overrides: Partial<WorkflowMarketplaceCatalogPackage> = {}): WorkflowMarketplaceCatalogPackage {
  return {
    configured_ref: 'main',
    contract_version: 1,
    description: 'Diagnostics and support workflows.',
    display_name: 'Laptop Support',
    id: 'laptop-support',
    identifier: 'company/laptop-support',
    license: 'MIT',
    package_digest: DIGEST,
    package_path: 'packages/laptop-support',
    publisher: 'Example Company',
    repository_url: 'https://example.test/company/workflows.git',
    resolved_commit: COMMIT,
    source_name: 'company',
    state: 'fresh',
    tags: ['diagnostics', 'support'],
    verified_at: NOW,
    version: '1.1.0',
    ...overrides
  }
}

function installedPackage(
  overrides: Partial<WorkflowMarketplaceInstalledPackage> = {}
): WorkflowMarketplaceInstalledPackage {
  return {
    actor: 'desktop:operator',
    configured_ref: 'main',
    contract_version: 1,
    distribution_digest: DIGEST,
    identity: { package_id: 'laptop-support', source_key: 'company' },
    installed_at: NOW,
    orphaned_source: false,
    package_path: 'packages/laptop-support',
    repository_url: 'https://example.test/company/workflows.git',
    resolved_commit: COMMIT,
    source_name: 'company',
    version: '1.1.0',
    workflow_paths: ['workflows/laptop-diagnostic.yml'],
    ...overrides
  }
}

function packageDetail(overrides: Partial<WorkflowMarketplacePackageDetail> = {}): WorkflowMarketplacePackageDetail {
  return {
    advisories: [{ code: 'provider_missing', message: 'Provider setup is required.', severity: 'advisory' }],
    blockers: [],
    configured_ref: 'main',
    contract_version: 1,
    description: 'Diagnostics and support workflows.',
    display_name: 'Laptop Support',
    external_requirements: {
      providers: ['openrouter'],
      runtimes: ['python'],
      secrets: ['SUPPORT_TOKEN'],
      services: ['ticketing'],
      tools: ['terminal']
    },
    id: 'laptop-support',
    identifier: 'company/laptop-support',
    identity: { package_id: 'laptop-support', source_key: 'company' },
    install_status: 'installed',
    installed: installedPackage(),
    license: 'MIT',
    package_digest: DIGEST,
    package_path: 'packages/laptop-support',
    publisher: 'Example Company',
    repository_url: 'https://example.test/company/workflows.git',
    resolved_commit: COMMIT,
    resources: [
      { path: 'commands/diagnose.md', types: ['command'] },
      { path: 'mcp/support.yml', types: ['mcp'] },
      { path: 'resources/guide.md', types: ['other'] },
      { path: 'scripts/check.py', types: ['script'] },
      { path: 'workflows/laptop-diagnostic.companion.yml', types: ['workflow_companion'] },
      { path: 'workflows/laptop-diagnostic.yml', types: ['workflow_definition'] }
    ],
    source_name: 'company',
    source_state: 'fresh',
    tags: ['diagnostics', 'support'],
    update_status: 'update_available',
    verified: true,
    verified_at: NOW,
    version: '1.1.0',
    workflows: [
      {
        approval_nodes: [],
        command_nodes: [],
        command_resources: ['commands/diagnose.md'],
        companion_path: 'workflows/laptop-diagnostic.companion.yml',
        compatibility: [
          {
            code: 'runtime_missing',
            message: 'Python runtime must be available.',
            severity: 'blocker'
          }
        ],
        definition_path: 'workflows/laptop-diagnostic.yml',
        external_requirements: {
          providers: ['openrouter'],
          runtimes: ['python'],
          secrets: ['SUPPORT_TOKEN'],
          services: ['ticketing'],
          tools: ['terminal']
        },
        local_mcp_servers: [],
        mcp_resource_files: [],
        mcp_resources: ['mcp/support.yml'],
        outward_action_nodes: [],
        package_digest: DIGEST,
        package_resource_set: 'package',
        providers: ['openrouter'],
        remote_mcp_servers: [],
        requested_skills: [],
        requested_tools: ['terminal'],
        required_secrets: ['SUPPORT_TOKEN'],
        risk_digest: 'e'.repeat(64),
        script_resources: ['scripts/check.py'],
        shell_or_script_nodes: [],
        trust_state: 'untrusted',
        workflow_name: 'Laptop diagnostic'
      }
    ],
    ...overrides
  }
}

function succeededDetail(detail = packageDetail(), id = OPERATION_ID): WorkflowMarketplaceOperation {
  return {
    created_at: NOW,
    error: null,
    finished_at: NOW,
    id,
    kind: 'package_detail',
    phase: 'completed',
    profile: 'support',
    progress: 100,
    result: { type: 'package_detail', value: detail },
    schema_version: 1,
    source_name: null,
    started_at: NOW,
    state: 'succeeded',
    updated_at: NOW
  }
}

function pendingDetail(id = OPERATION_ID): WorkflowMarketplaceOperation {
  return {
    created_at: NOW,
    error: null,
    finished_at: null,
    id,
    kind: 'package_detail',
    phase: 'queued',
    profile: 'support',
    progress: 0,
    result: null,
    schema_version: 1,
    source_name: null,
    started_at: null,
    state: 'pending',
    updated_at: NOW
  }
}

function succeededRefresh(sourceName: string): WorkflowMarketplaceOperation {
  return {
    created_at: NOW,
    error: null,
    finished_at: NOW,
    id: OPERATION_ID,
    kind: 'refresh',
    phase: 'completed',
    profile: 'support',
    progress: 100,
    result: {
      type: 'source_refresh',
      value: {
        diagnostic_code: null,
        message: null,
        package_count: 1,
        repository_url: 'https://example.test/company/workflows.git',
        resolved_commit: COMMIT,
        source_name: sourceName,
        state: 'fresh',
        verified_at: NOW
      }
    },
    schema_version: 1,
    source_name: sourceName,
    started_at: NOW,
    state: 'succeeded',
    updated_at: NOW
  }
}

function pendingRefresh(sourceName: string): WorkflowMarketplaceOperation {
  return {
    ...succeededRefresh(sourceName),
    error: null,
    finished_at: null,
    phase: 'queued',
    progress: 0,
    result: null,
    started_at: null,
    state: 'pending'
  }
}

function assessment(detail = packageDetail()) {
  return {
    advisories: detail.advisories,
    blockers: detail.blockers,
    external_requirements: detail.external_requirements,
    package_digest: detail.package_digest,
    package_resources: detail.resources.map(resource => resource.path),
    review_digest: 'f'.repeat(64),
    workflow_names: detail.workflows.map(workflow => workflow.workflow_name)
  }
}

function installReview(
  detail = packageDetail({ install_status: 'not_installed', installed: null, update_status: 'not_applicable' })
): WorkflowMarketplaceInstallReview {
  return {
    assessment: assessment(detail),
    candidate_digest: detail.package_digest,
    candidate_version: detail.version,
    confirmation_token: INSTALL_TOKEN,
    configured_ref: detail.configured_ref,
    file_changes: detail.resources.map(resource => ({
      candidate_digest: DIGEST,
      kind: 'added' as const,
      old_digest: null,
      old_path: null,
      path: resource.path
    })),
    identity: detail.identity,
    operation: 'install',
    package_path: detail.package_path,
    repository_url: detail.repository_url,
    resolved_commit: detail.resolved_commit,
    result: 'review_required',
    review_digest: 'f'.repeat(64),
    source_name: detail.source_name,
    workflow_reviews: detail.workflows
  }
}

function updateReview(detail = packageDetail()): WorkflowMarketplaceUpdateReview {
  const empty = { added: [] as string[], removed: [] as string[] }

  return {
    assessment: assessment(detail),
    candidate_commit: detail.resolved_commit,
    candidate_digest: detail.package_digest,
    candidate_version: detail.version,
    compatibility_changes: { added: [], removed: [] },
    confirmation_token: UPDATE_TOKEN,
    configured_ref: detail.configured_ref,
    file_changes: [],
    identity: detail.identity,
    old_commit: '1'.repeat(40),
    old_digest: '2'.repeat(64),
    old_version: detail.installed?.version ?? '1.0.0',
    operation: 'update',
    repository_url: detail.repository_url,
    requirement_changes: {
      providers: empty,
      runtimes: empty,
      secrets: empty,
      services: empty,
      tools: empty
    },
    result: 'update_available',
    review_digest: 'f'.repeat(64),
    risk_changes: { added: [], removed: [] },
    source_name: detail.source_name,
    workflow_changes: empty,
    workflow_reviews: detail.workflows
  }
}

function removeReview(item = installedPackage()): WorkflowMarketplaceRemoveReview {
  return {
    confirmation_token: REMOVE_TOKEN,
    current_commit: item.resolved_commit,
    current_version: item.version,
    distribution_digest: item.distribution_digest,
    identity: item.identity,
    operation: 'remove',
    result: 'review_required',
    review_digest: 'f'.repeat(64),
    workflow_names: ['Laptop diagnostic']
  }
}

function trustReview(item = installedPackage(), workflowName?: string): WorkflowMarketplaceTrustReview {
  const workflows = packageDetail().workflows.filter(
    workflow => workflowName === undefined || workflow.workflow_name === workflowName
  )

  return {
    confirmation_token: workflowName ? `${TRUST_TOKEN}-one` : TRUST_TOKEN,
    distribution_digest: item.distribution_digest,
    identity: item.identity,
    package_resources: packageDetail().resources.map(resource => resource.path),
    resolved_commit: item.resolved_commit,
    review_digest: 'f'.repeat(64),
    source_name: item.source_name,
    version: item.version,
    workflows
  }
}

function lifecycleOperation(
  kind: WorkflowMarketplaceOperationKind,
  result: WorkflowMarketplaceOperationResult,
  id = OPERATION_ID
): WorkflowMarketplaceOperation {
  return {
    created_at: NOW,
    error: null,
    finished_at: NOW,
    id,
    kind,
    phase: 'completed',
    profile: 'support',
    progress: 100,
    result,
    schema_version: 1,
    source_name: null,
    started_at: NOW,
    state: 'succeeded',
    updated_at: NOW
  } as WorkflowMarketplaceOperation
}

function pendingLifecycle(kind: WorkflowMarketplaceOperationKind, id = OPERATION_ID): WorkflowMarketplaceOperation {
  return {
    created_at: NOW,
    error: null,
    finished_at: null,
    id,
    kind,
    phase: 'queued',
    profile: 'support',
    progress: 0,
    result: null,
    schema_version: 1,
    source_name: null,
    started_at: null,
    state: 'pending',
    updated_at: NOW
  } as WorkflowMarketplaceOperation
}

function failedLifecycle(
  kind: WorkflowMarketplaceOperationKind,
  code = 'marketplace_operation_failed',
  id = OPERATION_ID
): WorkflowMarketplaceOperation {
  return {
    ...pendingLifecycle(kind, id),
    error: { code, message: 'Workflow marketplace operation failed.' },
    finished_at: NOW,
    phase: 'failed',
    progress: 80,
    started_at: NOW,
    state: 'failed'
  } as WorkflowMarketplaceOperation
}

function unsuccessfulRefresh(state: 'cancelled' | 'failed'): WorkflowMarketplaceOperation {
  return {
    ...succeededRefresh('company'),
    error:
      state === 'failed' ? { code: 'source_unavailable', message: 'Workflow marketplace operation failed.' } : null,
    phase: state,
    progress: 40,
    result: null,
    state
  } as WorkflowMarketplaceOperation
}

function terminalDetail(state: 'cancelled' | 'failed'): WorkflowMarketplaceOperation {
  const base = {
    created_at: NOW,
    finished_at: NOW,
    id: OPERATION_ID,
    kind: 'package_detail',
    profile: 'support',
    progress: 50,
    result: null,
    schema_version: 1,
    source_name: null,
    started_at: NOW,
    updated_at: NOW
  } as const

  if (state === 'failed') {
    return {
      ...base,
      error: { code: 'inspection_failed', message: 'Workflow marketplace operation failed.' },
      phase: 'failed',
      state: 'failed'
    }
  }

  return {
    ...base,
    error: null,
    phase: 'cancelled',
    state: 'cancelled'
  }
}

function page(
  items: WorkflowMarketplaceCatalogPackage[],
  nextOffset: null | number = null,
  query = '',
  source: null | string = null
) {
  return { items, limit: 50, next_offset: nextOffset, offset: 0, profile: 'support', query, source }
}

function renderWithProviders(
  node: ReactNode,
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
) {
  return {
    client,
    ...render(
      <I18nProvider configClient={null} initialLocale="en">
        <QueryClientProvider client={client}>{node}</QueryClientProvider>
      </I18nProvider>
    )
  }
}

function renderMarketplace(scope = scopeA) {
  return renderWithProviders(<WorkflowMarketplaceView scope={scope} />)
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

beforeEach(() => {
  api.capabilities.mockReset().mockResolvedValue({
    capabilities: ['sources', 'search', 'installed', 'updates', 'transactions', 'trust', 'operations'],
    profile: 'support',
    schema_version: 1
  })
  api.sources.mockReset().mockResolvedValue({
    profile: 'support',
    sources: [
      {
        attempted_at: NOW,
        diagnostic_code: null,
        enabled: true,
        message: null,
        name: 'company',
        ref: 'main',
        refresh_state: 'fresh',
        repository_url: 'https://example.test/company/workflows.git',
        resolved_commit: COMMIT,
        verified_at: NOW,
        verified_package_count: 1
      },
      {
        attempted_at: null,
        diagnostic_code: null,
        enabled: false,
        message: null,
        name: 'disabled-source',
        ref: null,
        refresh_state: null,
        repository_url: 'ssh://git@example.test/company/disabled.git',
        resolved_commit: null,
        verified_at: null,
        verified_package_count: 0
      }
    ]
  })
  api.search.mockReset().mockResolvedValue(page([packageItem()]))
  api.installed.mockReset().mockResolvedValue({ packages: [installedPackage()], profile: 'support' })
  api.inspect.mockReset().mockResolvedValue(succeededDetail())
  api.getOperation.mockReset().mockResolvedValue(succeededDetail())
  api.listOperations.mockReset().mockResolvedValue({ limit: 100, offset: 0, operations: [], profile: 'support' })
  api.cancelOperation.mockReset()
  api.checkUpdates.mockReset()
  api.confirmInstall.mockReset()
  api.confirmRemove.mockReset()
  api.confirmUpdate.mockReset()
  api.grantTrust.mockReset()
  api.prepareInstall.mockReset()
  api.prepareRemove.mockReset()
  api.prepareUpdate.mockReset()
  api.reviewTrust.mockReset()
  api.refreshSource.mockReset().mockImplementation((name: string) => Promise.resolve(succeededRefresh(name)))
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

describe('marketplace query keys', () => {
  it('keys every backend-owned projection by connection, profile, filter, page, and detail identity', () => {
    const scopeKey = 'remote-a::support'

    expect(marketplaceKeys.capabilities(scopeKey)).toEqual(['workflow-marketplace', scopeKey, 'capabilities'])
    expect(marketplaceKeys.sources(scopeKey)).toEqual(['workflow-marketplace', scopeKey, 'sources'])
    expect(marketplaceKeys.installed(scopeKey)).toEqual(['workflow-marketplace', scopeKey, 'installed'])
    expect(marketplaceKeys.operations(scopeKey)).toEqual(['workflow-marketplace', scopeKey, 'operations'])
    expect(marketplaceKeys.searchRoot(scopeKey)).toEqual(['workflow-marketplace', scopeKey, 'search'])
    expect(marketplaceKeys.search(scopeKey, { limit: 50, offset: 50, query: 'laptop', source: 'company' })).toEqual([
      'workflow-marketplace',
      scopeKey,
      'search',
      'laptop',
      'company',
      50,
      50
    ])
    expect(marketplaceKeys.detail(scopeKey, 'company', 'laptop-support')).toEqual([
      'workflow-marketplace',
      scopeKey,
      'detail',
      'company',
      'laptop-support'
    ])
  })
})

describe('WorkflowMarketplaceView', () => {
  it('shows feature-detected Refresh and Manage Sources actions and returns focus after closing', async () => {
    renderMarketplace()

    const manage = await screen.findByRole('button', { name: 'Manage Sources' })
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeTruthy()
    manage.focus()
    fireEvent.click(manage)
    expect(await screen.findByRole('dialog', { name: 'Workflow sources' })).toBeTruthy()
    const dialog = screen.getByRole('dialog', { name: 'Workflow sources' })
    fireEvent.click(within(dialog).getAllByRole('button', { name: 'Close' })[0])
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Workflow sources' })).toBeNull())
    await waitFor(() => expect(globalThis.document.activeElement).toBe(manage))
  })

  it('does not imply source operations when only catalog search is supported', async () => {
    api.capabilities.mockResolvedValueOnce({ capabilities: ['search'], profile: 'support', schema_version: 1 })
    renderMarketplace()

    expect(await screen.findByRole('searchbox', { name: 'Search workflow packages' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Refresh' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Manage Sources' })).toBeNull()
    expect(screen.getByText('Upgrade Hermes to manage and refresh workflow sources.')).toBeTruthy()
  })

  it('refreshes enabled sources sequentially from the toolbar and skips disabled sources', async () => {
    const order: string[] = []
    api.refreshSource.mockImplementation(async (name: string) => {
      order.push(name)

      return succeededRefresh(name)
    })
    renderMarketplace()

    fireEvent.click(await screen.findByRole('button', { name: 'Refresh' }))
    await waitFor(() => expect(order).toEqual(['company']))
    expect(api.refreshSource).toHaveBeenCalledWith('company', scopeA)
    expect(api.refreshSource).not.toHaveBeenCalledWith('disabled-source', expect.anything())
  })

  it.each([
    ['failed', 'Failed'],
    ['cancelled', 'Cancelled']
  ] as const)('keeps a %s toolbar refresh visible with a path to exact recovery', async (state, label) => {
    api.refreshSource.mockResolvedValue(unsuccessfulRefresh(state))
    renderMarketplace()

    fireEvent.click(await screen.findByRole('button', { name: 'Refresh' }))

    const results = await screen.findByRole('status', { name: 'Workflow source refresh results' })
    expect(within(results).getByText(`company: ${label}`)).toBeTruthy()
    expect(within(results).getByRole('button', { name: 'Manage Sources' })).toBeTruthy()
  })

  it('keeps an evicted toolbar refresh visible with a path to exact recovery', async () => {
    api.refreshSource.mockResolvedValue(pendingRefresh('company'))
    api.getOperation.mockRejectedValue(
      new WorkflowMarketplaceApiError('marketplace_operation_not_found', 404, 'Operation not found.')
    )
    renderMarketplace()

    const refresh = await screen.findByRole('button', { name: 'Refresh' })
    vi.useFakeTimers()
    fireEvent.click(refresh)
    await act(async () => vi.advanceTimersByTimeAsync(500))

    const results = screen.getByRole('status', { name: 'Workflow source refresh results' })
    expect(
      within(results).getByText('company: Refresh status expired. Source data has been reconciled; retry if needed.')
    ).toBeTruthy()
    expect(within(results).getByRole('button', { name: 'Manage Sources' })).toBeTruthy()
  })

  it('aborts an old-scope Refresh sequence before admitting its next source', async () => {
    const first = deferred<WorkflowMarketplaceOperation>()
    api.sources.mockResolvedValue({
      profile: 'support',
      sources: [
        {
          attempted_at: NOW,
          diagnostic_code: null,
          enabled: true,
          message: null,
          name: 'company',
          ref: 'main',
          refresh_state: 'fresh',
          repository_url: 'https://example.test/company/workflows.git',
          resolved_commit: COMMIT,
          verified_at: NOW,
          verified_package_count: 1
        },
        {
          attempted_at: null,
          diagnostic_code: null,
          enabled: true,
          message: null,
          name: 'team',
          ref: null,
          refresh_state: null,
          repository_url: 'https://example.test/team/workflows.git',
          resolved_commit: null,
          verified_at: null,
          verified_package_count: 0
        }
      ]
    })
    api.refreshSource.mockImplementation((name: string) =>
      name === 'company' ? first.promise : Promise.resolve(succeededRefresh(name))
    )
    const view = renderMarketplace(scopeA)

    fireEvent.click(await screen.findByRole('button', { name: 'Refresh' }))
    await waitFor(() => expect(api.refreshSource).toHaveBeenCalledWith('company', scopeA))

    view.rerender(
      <I18nProvider configClient={null} initialLocale="en">
        <QueryClientProvider client={view.client}>
          <WorkflowMarketplaceView scope={{ connectionId: 'remote-b', profile: 'support' }} />
        </QueryClientProvider>
      </I18nProvider>
    )

    const newScopeRefresh = await screen.findByRole('button', { name: 'Refresh' })
    expect(newScopeRefresh.getAttribute('aria-busy')).toBe('false')
    fireEvent.click(newScopeRefresh)
    await waitFor(() =>
      expect(api.refreshSource).toHaveBeenCalledWith('company', {
        connectionId: 'remote-b',
        profile: 'support'
      })
    )
    await act(async () => first.resolve(succeededRefresh('company')))

    expect(api.refreshSource).not.toHaveBeenCalledWith('team', scopeA)
    expect(api.refreshSource).toHaveBeenCalledWith('team', { connectionId: 'remote-b', profile: 'support' })
  })
  it('feature-detects before search and shows upgrade guidance for an older backend', async () => {
    const probe = deferred<never>()
    api.capabilities.mockReturnValue(probe.promise)

    renderMarketplace()

    expect(screen.getByRole('status', { name: 'Checking workflow marketplace availability' })).toBeTruthy()
    expect(api.search).not.toHaveBeenCalled()

    api.capabilities.mockReset().mockRejectedValueOnce({ code: 'marketplace_unsupported' })
    cleanup()
    renderMarketplace()

    expect(await screen.findByText('Upgrade Hermes to use Workflow Marketplace')).toBeTruthy()
    expect(api.search).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /install/i })).toBeNull()
  })

  it.each([
    [{ code: '401', status: 401 }, 'Sign in to browse workflow packages'],
    [{ code: 'marketplace_network_error', status: 0 }, 'Could not load Workflow Marketplace']
  ])('separates authentication and network errors and offers a safe retry', async (error, title) => {
    api.capabilities.mockRejectedValueOnce(error).mockResolvedValueOnce({
      capabilities: ['sources', 'search', 'installed', 'updates', 'transactions', 'trust', 'operations'],
      profile: 'support',
      schema_version: 1
    })

    renderMarketplace()

    expect(await screen.findByText(title)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('searchbox', { name: 'Search workflow packages' })).toBeTruthy()
  })

  it('shows loading, empty, and search no-match states without rendering raw response data', async () => {
    const pending = deferred<ReturnType<typeof page>>()
    api.search
      .mockReturnValueOnce(pending.promise)
      .mockImplementation((query: string) => Promise.resolve(page([], null, query)))

    renderMarketplace()

    expect(await screen.findByRole('status', { name: 'Loading workflow packages' })).toBeTruthy()
    pending.resolve(page([]))
    expect(await screen.findByText('No workflow packages available')).toBeTruthy()

    fireEvent.change(screen.getByRole('searchbox', { name: 'Search workflow packages' }), {
      target: { value: '<script>secret raw payload</script>' }
    })

    expect(await screen.findByText('No packages match your search')).toBeTruthy()
    expect(screen.queryByText('secret raw payload')).toBeNull()
  })

  it('keeps browsing visible while installed package status is loading', async () => {
    const pending = deferred<Awaited<ReturnType<typeof api.installed>>>()
    api.installed.mockReturnValueOnce(pending.promise)

    renderMarketplace()

    expect(await screen.findByRole('option', { name: /Laptop Support/ })).toBeTruthy()
    expect(screen.getByRole('status', { name: 'Loading installed package status' })).toBeTruthy()
    expect(screen.queryByText(/Installed v/)).toBeNull()

    pending.resolve({ packages: [installedPackage()], profile: 'support' })
    expect(await screen.findByText('Installed v1.1.0')).toBeTruthy()
  })

  it.each([
    [
      { code: '401', message: 'https://operator:secret@example.test/repo.git', status: 401 },
      'Sign in to compare installed packages'
    ],
    [
      { code: 'marketplace_network_error', message: '/private/tmp/checkout', status: 0 },
      'Installed package status unavailable'
    ],
    [
      { code: 'marketplace_invalid_response', message: 'token=secret', status: 502 },
      'Installed package status unavailable'
    ]
  ])('keeps browsing visible when installed provenance fails with %#', async (error, title) => {
    api.installed.mockRejectedValueOnce(error).mockResolvedValueOnce({
      packages: [installedPackage()],
      profile: 'support'
    })

    renderMarketplace()

    expect(await screen.findByRole('option', { name: /Laptop Support/ })).toBeTruthy()
    const warning = screen.getByRole('alert', { name: title })
    expect(within(warning).queryByText(/operator:secret|private\/tmp|token=secret/)).toBeNull()
    expect(screen.queryByText(/Installed v/)).toBeNull()

    fireEvent.click(within(warning).getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Installed v1.1.0')).toBeTruthy()
  })

  it('distinguishes an unavailable installed capability from a provenance query failure', async () => {
    api.capabilities.mockResolvedValueOnce({
      capabilities: ['sources', 'search', 'operations'],
      profile: 'support',
      schema_version: 1
    })

    renderMarketplace()

    expect(await screen.findByRole('option', { name: /Laptop Support/ })).toBeTruthy()
    const notice = screen.getByRole('status', { name: 'Installed package status unavailable' })
    expect(within(notice).queryByRole('button', { name: 'Retry' })).toBeNull()
    expect(api.installed).not.toHaveBeenCalled()
  })

  it('retains cached installed card provenance through a background failure and replaces it after retry', async () => {
    api.installed
      .mockResolvedValueOnce({ packages: [installedPackage()], profile: 'support' })
      .mockRejectedValueOnce(
        new WorkflowMarketplaceApiError(
          'marketplace_network_error',
          0,
          'access_token=secret /private/tmp/installed-cache'
        )
      )
      .mockResolvedValueOnce({
        packages: [installedPackage({ version: '1.2.0' })],
        profile: 'support'
      })

    const rendered = renderMarketplace()
    expect(await screen.findByText('Installed v1.1.0')).toBeTruthy()

    await act(async () => {
      await rendered.client.refetchQueries({ queryKey: marketplaceKeys.installed('remote-a::support') })
    })

    const warning = await screen.findByRole('alert', { name: 'Installed package status unavailable' })
    expect(screen.getByText('Installed v1.1.0')).toBeTruthy()
    expect(screen.getByRole('option', { name: /Laptop Support/ })).toBeTruthy()
    expect(within(warning).queryByText(/access_token|private\/tmp/)).toBeNull()

    fireEvent.click(within(warning).getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('Installed v1.2.0')).toBeTruthy()
    expect(screen.queryByText('Installed v1.1.0')).toBeNull()
  })

  it('preserves backend ordering, reports stale partial results, filters sources, and pages within API bounds', async () => {
    const first = packageItem({ display_name: 'Zeta Support', identifier: 'company/zeta', id: 'zeta' })

    const second = packageItem({
      display_name: 'Alpha Support',
      id: 'alpha',
      identifier: 'company/alpha',
      state: 'stale'
    })

    api.search.mockImplementation((_query: string, _scope: unknown, options: { offset?: number; source?: string }) =>
      Promise.resolve(
        options.offset === 50
          ? {
              ...page([packageItem({ display_name: 'Page two', id: 'page-two', identifier: 'company/page-two' })]),
              offset: 50
            }
          : page([first, second], 50, '', options.source ?? null)
      )
    )

    renderMarketplace()

    const listbox = await screen.findByRole('listbox', { name: 'Workflow packages' })
    expect(
      within(listbox)
        .getAllByRole('option')
        .map(option => option.textContent)
    ).toEqual([expect.stringContaining('Zeta Support'), expect.stringContaining('Alpha Support')])
    expect(screen.getByRole('status', { name: 'Stale marketplace results' })).toBeTruthy()
    expect(screen.getByText(/results may be partial/i)).toBeTruthy()
    expect(api.inspect).not.toHaveBeenCalled()
    expect(screen.queryByText('Update available')).toBeNull()
    expect(screen.queryByText(/workflow compatibility/i)).toBeNull()
    expect(screen.queryByText(/^\d+ workflows?$/)).toBeNull()

    fireEvent.pointerDown(screen.getByRole('combobox', { name: 'Source' }), {
      button: 0,
      ctrlKey: false,
      pointerType: 'mouse'
    })
    expect((await screen.findByRole('option', { name: /disabled-source/i })).getAttribute('aria-disabled')).toBe('true')
    fireEvent.click(screen.getByRole('option', { name: 'company' }))
    await waitFor(() =>
      expect(api.search).toHaveBeenLastCalledWith('', scopeA, { limit: 50, offset: 0, source: 'company' })
    )
    await screen.findByRole('listbox', { name: 'Workflow packages' })

    fireEvent.click(screen.getByRole('button', { name: 'Next page' }))
    expect(await screen.findByText('Page two')).toBeTruthy()
    expect(api.search).toHaveBeenLastCalledWith('', scopeA, { limit: 50, offset: 50, source: 'company' })
  })

  it.each([
    ['source_authentication_failed', 'Source authentication required'],
    ['source_unavailable', 'Source unavailable']
  ])('distinguishes %s and retries without exposing backend detail', async (code, title) => {
    api.search
      .mockRejectedValueOnce({
        code,
        message: 'https://operator:secret@example.test/private.git'
      })
      .mockResolvedValueOnce(page([packageItem()]))

    renderMarketplace()

    expect(await screen.findByText(title)).toBeTruthy()
    expect(screen.queryByText(/operator:secret/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('option', { name: /Laptop Support/ })).toBeTruthy()
  })

  it('shows authoritative package detail, resource categories, requirements, advisories, and badges', async () => {
    renderMarketplace()

    fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))

    const detail = await screen.findByRole('region', { name: 'Laptop Support package details' })
    const candidate = within(detail).getByRole('region', { name: 'Candidate package identity' })
    expect(within(detail).getAllByText('company/laptop-support')).toHaveLength(2)
    expect(within(candidate).getByText('Example Company')).toBeTruthy()
    expect(within(candidate).getByText('MIT')).toBeTruthy()
    expect(within(candidate).getByText('main')).toBeTruthy()
    expect(within(candidate).getByText(COMMIT)).toBeTruthy()
    expect(within(candidate).getByText(NOW)).toBeTruthy()
    expect(within(candidate).getByRole('link', { name: /example\.test\/company\/workflows\.git/ })).toBeTruthy()
    expect(within(detail).getByText('Update available')).toBeTruthy()
    expect(within(detail).getByText('Laptop diagnostic')).toBeTruthy()
    expect(within(detail).getByText('workflows/laptop-diagnostic.companion.yml')).toBeTruthy()
    expect(within(detail).getByText('Python runtime must be available.')).toBeTruthy()
    expect(within(detail).getByText('commands/diagnose.md')).toBeTruthy()
    expect(within(detail).getByText('scripts/check.py')).toBeTruthy()
    expect(within(detail).getByText('mcp/support.yml')).toBeTruthy()
    expect(within(detail).getByText('resources/guide.md')).toBeTruthy()
    expect(within(detail).getByText('Provider setup is required.')).toBeTruthy()
    expect(within(detail).getByText('SUPPORT_TOKEN')).toBeTruthy()
    expect(api.inspect).toHaveBeenCalledWith('company', 'laptop-support', scopeA)
  })

  it('polls a bounded detail operation and never turns file, redacted, SSH, or SCP identities into links', async () => {
    api.inspect.mockResolvedValue(pendingDetail())
    api.getOperation.mockResolvedValue(
      succeededDetail(packageDetail({ repository_url: 'file:///REDACTED', update_status: 'current' }))
    )

    renderMarketplace()
    fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))

    expect(await screen.findByText('file:///REDACTED')).toBeTruthy()
    expect(screen.queryByRole('link', { name: 'file:///REDACTED' })).toBeNull()
    expect(api.getOperation).toHaveBeenCalledWith(OPERATION_ID, scopeA)

    for (const repository_url of ['ssh://git@example.test/team/repo.git', 'git@example.test:team/repo.git']) {
      cleanup()
      renderWithProviders(
        <MarketplacePackageDetail
          detail={packageDetail({ installed: installedPackage({ repository_url }), repository_url })}
        />
      )
      expect(screen.getAllByText(repository_url)).toHaveLength(2)
      expect(screen.queryByRole('link', { name: repository_url })).toBeNull()
    }
  })

  it.each(['failed', 'cancelled'] as const)(
    'treats an initially %s detail operation as terminal without polling',
    async state => {
      api.inspect.mockResolvedValueOnce(terminalDetail(state)).mockResolvedValueOnce(succeededDetail())

      renderMarketplace()
      fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))

      expect(await screen.findByText('Could not inspect this package')).toBeTruthy()
      expect(screen.queryByRole('status', { name: 'Loading workflow package details' })).toBeNull()
      expect(api.getOperation).not.toHaveBeenCalled()

      fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
      expect(await screen.findByRole('region', { name: 'Laptop Support package details' })).toBeTruthy()
      expect(api.getOperation).not.toHaveBeenCalled()
    }
  )

  it('stops a rejected detail poll and retries that operation without leaking its error', async () => {
    const statusRetry = deferred<WorkflowMarketplaceOperation>()

    api.inspect.mockResolvedValue(pendingDetail())
    api.getOperation.mockRejectedValue(
      new WorkflowMarketplaceApiError('marketplace_network_error', 0, 'access_token=secret /private/tmp/operation')
    )

    renderMarketplace()
    fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))

    expect(await screen.findByText('Could not inspect this package')).toBeTruthy()
    expect(screen.queryByText(/access_token|private\/tmp/)).toBeNull()
    expect(screen.queryByRole('status', { name: 'Loading workflow package details' })).toBeNull()
    const callsAfterFailure = api.getOperation.mock.calls.length

    await new Promise(resolve => setTimeout(resolve, 600))
    expect(api.getOperation).toHaveBeenCalledTimes(callsAfterFailure)

    api.getOperation.mockReturnValue(statusRetry.promise)
    const retry = screen.getByRole('button', { name: 'Retry' }) as HTMLButtonElement
    fireEvent.click(retry)
    fireEvent.click(retry)

    expect(api.getOperation).toHaveBeenCalledTimes(callsAfterFailure + 1)
    expect(await screen.findByRole('status', { name: 'Loading workflow package details' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull()
    statusRetry.resolve(succeededDetail())

    expect(await screen.findByRole('region', { name: 'Laptop Support package details' })).toBeTruthy()
    expect(api.inspect).toHaveBeenCalledTimes(1)
  })

  it('serializes a replacement inspection when the pending detail operation was evicted', async () => {
    const replacementOperationId = `wmop_${'e'.repeat(12)}_${'f'.repeat(32)}`
    const replacement = deferred<WorkflowMarketplaceOperation>()

    api.inspect.mockResolvedValueOnce(pendingDetail()).mockReturnValue(replacement.promise)
    api.getOperation.mockImplementation((id: string) =>
      id === OPERATION_ID
        ? Promise.reject(
            new WorkflowMarketplaceApiError(
              'marketplace_operation_not_found',
              404,
              'Workflow marketplace request failed.'
            )
          )
        : Promise.resolve(succeededDetail(packageDetail(), replacementOperationId))
    )

    renderMarketplace()
    fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))

    expect(await screen.findByText('Could not inspect this package')).toBeTruthy()
    const retry = screen.getByRole('button', { name: 'Retry' }) as HTMLButtonElement
    fireEvent.click(retry)
    fireEvent.click(retry)

    expect(api.inspect).toHaveBeenCalledTimes(2)
    await waitFor(() => expect(retry.disabled).toBe(true))
    expect(retry.getAttribute('aria-busy')).toBe('true')

    replacement.resolve(pendingDetail(replacementOperationId))

    expect(await screen.findByRole('region', { name: 'Laptop Support package details' })).toBeTruthy()
    expect(api.getOperation.mock.calls.map(([id]) => id)).toEqual([OPERATION_ID, replacementOperationId])
  })

  it('re-enables an evicted-operation Retry after its replacement inspection fails', async () => {
    const replacement = deferred<WorkflowMarketplaceOperation>()

    api.inspect
      .mockResolvedValueOnce(pendingDetail())
      .mockReturnValueOnce(replacement.promise)
      .mockResolvedValueOnce(succeededDetail())
    api.getOperation.mockRejectedValue(
      new WorkflowMarketplaceApiError('marketplace_operation_not_found', 404, 'Workflow marketplace request failed.')
    )

    renderMarketplace()
    fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))

    const retry = (await screen.findByRole('button', { name: 'Retry' })) as HTMLButtonElement
    fireEvent.click(retry)
    await waitFor(() => expect(retry.disabled).toBe(true))

    replacement.reject(
      new WorkflowMarketplaceApiError('marketplace_network_error', 0, 'Workflow marketplace request failed.')
    )

    const retryAgain = (await screen.findByRole('button', { name: 'Retry' })) as HTMLButtonElement
    await waitFor(() => expect(retryAgain.disabled).toBe(false))
    fireEvent.click(retryAgain)

    expect(await screen.findByRole('region', { name: 'Laptop Support package details' })).toBeTruthy()
    expect(api.inspect).toHaveBeenCalledTimes(3)
  })

  it('does not let an in-flight replacement block recovery after the scope changes', async () => {
    const oldReplacement = deferred<WorkflowMarketplaceOperation>()

    const newDetail = packageDetail({
      display_name: 'New backend package',
      id: 'new',
      identifier: 'other/new',
      identity: { package_id: 'new', source_key: 'other' },
      source_name: 'other'
    })

    api.search.mockImplementation((_query: string, scope: typeof scopeA) =>
      scope.connectionId === 'remote-a'
        ? Promise.resolve(page([packageItem()]))
        : Promise.resolve(
            page([
              packageItem({
                display_name: 'New backend package',
                id: 'new',
                identifier: 'other/new',
                source_name: 'other'
              })
            ])
          )
    )
    api.inspect
      .mockResolvedValueOnce(pendingDetail())
      .mockReturnValueOnce(oldReplacement.promise)
      .mockResolvedValueOnce(terminalDetail('failed'))
      .mockResolvedValueOnce(succeededDetail(newDetail))
    api.getOperation.mockRejectedValue(
      new WorkflowMarketplaceApiError('marketplace_operation_not_found', 404, 'Workflow marketplace request failed.')
    )

    const rendered = renderMarketplace()
    fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }))

    rendered.rerender(
      <I18nProvider configClient={null} initialLocale="en">
        <QueryClientProvider client={rendered.client}>
          <WorkflowMarketplaceView scope={{ connectionId: 'remote-b', profile: 'other' }} />
        </QueryClientProvider>
      </I18nProvider>
    )

    fireEvent.click(await screen.findByRole('option', { name: /New backend package/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('region', { name: 'New backend package package details' })).toBeTruthy()
    expect(api.inspect).toHaveBeenCalledTimes(4)
  })

  it('sanitizes detail failures and retries a fresh inspection', async () => {
    api.inspect
      .mockRejectedValueOnce(new Error('access_token=secret /private/tmp/checkout'))
      .mockResolvedValueOnce(succeededDetail())

    renderMarketplace()
    fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))

    expect(await screen.findByText('Could not inspect this package')).toBeTruthy()
    expect(screen.queryByText(/access_token|private\/tmp/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('region', { name: 'Laptop Support package details' })).toBeTruthy()
    expect(api.inspect).toHaveBeenCalledTimes(2)
  })

  it('clears selection and ignores an old delayed detail when connection and profile change', async () => {
    const oldDetail = deferred<WorkflowMarketplaceOperation>()
    api.search.mockImplementation((_query: string, scope: typeof scopeA) =>
      scope.connectionId === 'remote-a'
        ? Promise.resolve(page([packageItem()]))
        : Promise.resolve(
            page([packageItem({ display_name: 'New backend package', id: 'new', identifier: 'other/new' })])
          )
    )
    api.inspect.mockImplementation((_source: string, _id: string, scope: typeof scopeA) =>
      scope.connectionId === 'remote-a' ? oldDetail.promise : Promise.resolve(succeededDetail())
    )

    const rendered = renderMarketplace()
    const oldOption = await screen.findByRole('option', { name: /Laptop Support/ })
    oldOption.focus()
    fireEvent.click(oldOption)
    expect(await screen.findByRole('status', { name: 'Loading workflow package details' })).toBeTruthy()

    rendered.rerender(
      <I18nProvider configClient={null} initialLocale="en">
        <QueryClientProvider client={rendered.client}>
          <WorkflowMarketplaceView scope={{ connectionId: 'remote-b', profile: 'other' }} />
        </QueryClientProvider>
      </I18nProvider>
    )

    expect(await screen.findByText('New backend package')).toBeTruthy()
    oldDetail.resolve(succeededDetail())
    await act(async () => Promise.resolve())
    expect(screen.queryByRole('region', { name: 'Laptop Support package details' })).toBeNull()
    expect(screen.queryByRole('option', { name: /Laptop Support/ })).toBeNull()
    await waitFor(() =>
      expect(window.document.activeElement).toBe(screen.getByRole('searchbox', { name: 'Search workflow packages' }))
    )
  })

  it('supports listbox keyboard selection and clears detail when filtering removes the selected package', async () => {
    api.search.mockImplementation((query: string) =>
      Promise.resolve(
        page(
          query
            ? []
            : [
                packageItem(),
                packageItem({ display_name: 'Inbox Productivity', id: 'inbox', identifier: 'company/inbox' })
              ],
          null,
          query
        )
      )
    )

    renderMarketplace()
    const options = await screen.findAllByRole('option', { name: /Support|Productivity/ })
    options[0].focus()
    fireEvent.keyDown(options[0], { key: 'ArrowDown' })

    expect(options[1].getAttribute('aria-selected')).toBe('true')
    expect(window.document.activeElement).toBe(options[1])

    expect(fireEvent.keyDown(options[1], { key: 'Home' })).toBe(false)
    expect(options[0].getAttribute('aria-selected')).toBe('true')
    expect(window.document.activeElement).toBe(options[0])
    expect(fireEvent.keyDown(options[0], { key: 'End' })).toBe(false)
    expect(options[1].getAttribute('aria-selected')).toBe('true')
    expect(fireEvent.keyDown(options[1], { key: ' ' })).toBe(false)
    expect(fireEvent.keyDown(options[1], { key: 'Enter' })).toBe(false)

    fireEvent.change(screen.getByRole('searchbox', { name: 'Search workflow packages' }), {
      target: { value: 'nothing' }
    })
    expect(await screen.findByText('No packages match your search')).toBeTruthy()
    expect(screen.queryByRole('region', { name: /package details/ })).toBeNull()
  })

  it('returns focus to search when a refetch removes the focused selected package', async () => {
    api.search.mockResolvedValueOnce(page([packageItem()])).mockResolvedValueOnce(page([]))
    const rendered = renderMarketplace()
    const option = await screen.findByRole('option', { name: /Laptop Support/ })
    option.focus()
    fireEvent.click(option)

    await act(async () => {
      await rendered.client.refetchQueries({
        queryKey: marketplaceKeys.search('remote-a::support', {
          limit: 50,
          offset: 0,
          query: '',
          source: null
        })
      })
    })

    expect(await screen.findByText('No workflow packages available')).toBeTruthy()
    await waitFor(() =>
      expect(window.document.activeElement).toBe(screen.getByRole('searchbox', { name: 'Search workflow packages' }))
    )
  })

  it('uses narrow list-to-detail navigation with a real back button and returns focus', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      addEventListener: vi.fn(),
      matches: query.includes('max-width'),
      media: query,
      removeEventListener: vi.fn()
    }))

    renderMarketplace()
    const packageOption = await screen.findByRole('option', { name: /Laptop Support/ })
    packageOption.focus()
    fireEvent.click(packageOption)

    const back = await screen.findByRole('button', { name: 'Back to packages' })
    await waitFor(() => expect(window.document.activeElement).toBe(back))
    expect(screen.queryByRole('listbox', { name: 'Workflow packages' })).toBeNull()
    fireEvent.click(back)

    const restoredOption = await screen.findByRole('option', { name: /Laptop Support/ })
    await waitFor(() => expect(window.document.activeElement).toBe(restoredOption))
    expect(screen.getByRole('listbox', { name: 'Workflow packages' })).toBeTruthy()
  })

  it('focuses narrow detail after keyboard selection and Escape returns focus to the package', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      addEventListener: vi.fn(),
      matches: query.includes('max-width'),
      media: query,
      removeEventListener: vi.fn()
    }))

    renderMarketplace()
    const packageOption = await screen.findByRole('option', { name: /Laptop Support/ })
    packageOption.focus()
    fireEvent.keyDown(packageOption, { key: 'Enter' })

    const back = await screen.findByRole('button', { name: 'Back to packages' })
    await waitFor(() => expect(window.document.activeElement).toBe(back))
    fireEvent.keyDown(back, { key: 'Escape' })

    const restoredOption = await screen.findByRole('option', { name: /Laptop Support/ })
    await waitFor(() => expect(window.document.activeElement).toBe(restoredOption))
  })

  it('returns narrow focus to search when the selected package disappears', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({
      addEventListener: vi.fn(),
      matches: query.includes('max-width'),
      media: query,
      removeEventListener: vi.fn()
    }))
    api.search.mockResolvedValueOnce(page([packageItem()])).mockResolvedValueOnce(page([]))

    const rendered = renderMarketplace()
    const packageOption = await screen.findByRole('option', { name: /Laptop Support/ })
    fireEvent.click(packageOption)
    const back = await screen.findByRole('button', { name: 'Back to packages' })
    await waitFor(() => expect(window.document.activeElement).toBe(back))

    await act(async () => {
      await rendered.client.refetchQueries({
        queryKey: marketplaceKeys.search('remote-a::support', {
          limit: 50,
          offset: 0,
          query: '',
          source: null
        })
      })
    })

    expect(await screen.findByText('No workflow packages available')).toBeTruthy()
    await waitFor(() =>
      expect(window.document.activeElement).toBe(screen.getByRole('searchbox', { name: 'Search workflow packages' }))
    )
  })
})

describe('browse presentation components', () => {
  it('keeps loose workflows visible while installed provenance loads', async () => {
    const pending = deferred<Awaited<ReturnType<typeof api.installed>>>()
    api.installed.mockReturnValueOnce(pending.promise)

    renderWithProviders(
      <InstalledPackages scope={scopeA}>
        <div>Loose profile workflow</div>
      </InstalledPackages>
    )

    expect(screen.getByText('Loose profile workflow')).toBeTruthy()
    expect(await screen.findByRole('status', { name: 'Loading installed package provenance' })).toBeTruthy()

    pending.resolve({ packages: [], profile: 'support' })
    await waitFor(() =>
      expect(screen.queryByRole('status', { name: 'Loading installed package provenance' })).toBeNull()
    )
  })

  it.each([
    [
      { code: '401', message: 'https://operator:secret@example.test/repo.git', status: 401 },
      'Sign in to load installed package provenance'
    ],
    [
      { code: 'marketplace_network_error', message: '/private/tmp/checkout', status: 0 },
      'Could not load installed package provenance'
    ],
    [
      { code: 'marketplace_invalid_response', message: 'token=secret', status: 502 },
      'Could not load installed package provenance'
    ]
  ])('keeps loose workflows visible and retries installed provenance failure %#', async (error, title) => {
    api.installed.mockRejectedValueOnce(error).mockResolvedValueOnce({
      packages: [installedPackage()],
      profile: 'support'
    })

    renderWithProviders(
      <InstalledPackages scope={scopeA}>
        <div>Loose profile workflow</div>
      </InstalledPackages>
    )

    expect(screen.getByText('Loose profile workflow')).toBeTruthy()
    const alert = await screen.findByRole('alert', { name: title })
    expect(within(alert).queryByText(/operator:secret|private\/tmp|token=secret/)).toBeNull()
    expect(screen.queryByRole('region', { name: 'Installed marketplace packages' })).toBeNull()

    fireEvent.click(within(alert).getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('company/laptop-support')).toBeTruthy()
  })

  it('retains cached installed groups through a background failure and replaces them after retry', async () => {
    api.installed
      .mockResolvedValueOnce({ packages: [installedPackage()], profile: 'support' })
      .mockRejectedValueOnce(
        new WorkflowMarketplaceApiError(
          'marketplace_network_error',
          0,
          'access_token=secret /private/tmp/installed-cache'
        )
      )
      .mockResolvedValueOnce({
        packages: [
          installedPackage({
            version: '1.2.0',
            workflow_paths: ['workflows/laptop-diagnostic-v2.yml']
          })
        ],
        profile: 'support'
      })

    const rendered = renderWithProviders(
      <InstalledPackages scope={scopeA}>
        <div>Loose profile workflow</div>
      </InstalledPackages>
    )

    expect(await screen.findByText('workflows/laptop-diagnostic.yml')).toBeTruthy()

    await act(async () => {
      await rendered.client.refetchQueries({ queryKey: marketplaceKeys.installed('remote-a::support') })
    })

    const warning = await screen.findByRole('alert', { name: 'Could not load installed package provenance' })
    expect(screen.getByText('workflows/laptop-diagnostic.yml')).toBeTruthy()
    expect(screen.getByText('Loose profile workflow')).toBeTruthy()
    expect(within(warning).queryByText(/access_token|private\/tmp/)).toBeNull()

    fireEvent.click(within(warning).getByRole('button', { name: 'Retry' }))
    expect(await screen.findByText('workflows/laptop-diagnostic-v2.yml')).toBeTruthy()
    expect(screen.getByText('v1.2.0')).toBeTruthy()
    expect(screen.queryByText('workflows/laptop-diagnostic.yml')).toBeNull()
  })

  it('keeps the loose workflow catalog available when installed-package capability detection is unsupported', async () => {
    api.capabilities.mockRejectedValueOnce({ code: 'marketplace_unsupported' })

    renderWithProviders(
      <InstalledPackages scope={scopeA}>
        <div>Loose profile workflow</div>
      </InstalledPackages>
    )

    expect(screen.getByText('Loose profile workflow')).toBeTruthy()
    await waitFor(() => expect(api.capabilities).toHaveBeenCalledWith(scopeA))
    expect(await screen.findByRole('status', { name: 'Installed package provenance unavailable' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Retry' })).toBeNull()
    expect(api.installed).not.toHaveBeenCalled()
  })

  it('keeps the existing loose-workflow catalog visible beside installed package provenance', async () => {
    api.installed.mockResolvedValueOnce({ packages: [installedPackage({ orphaned_source: true })], profile: 'support' })
    renderWithProviders(
      <InstalledPackages scope={scopeA}>
        <div>Loose project workflow</div>
      </InstalledPackages>
    )

    expect(await screen.findByText('company/laptop-support')).toBeTruthy()
    expect(screen.getByText('1 workflow')).toBeTruthy()
    expect(screen.getByText('Source unavailable')).toBeTruthy()
    expect(screen.getByText('Loose project workflow')).toBeTruthy()
  })

  it('shows installed provenance on cards without fabricating an update state', () => {
    renderWithProviders(
      <MarketplacePackageList
        installedPackages={[installedPackage({ distribution_digest: 'f'.repeat(64), version: '1.0.0' })]}
        items={[packageItem()]}
        onSelect={vi.fn()}
        selectedIdentifier={null}
      />
    )

    expect(screen.getByText('Installed v1.0.0')).toBeTruthy()
    expect(screen.queryByText('Current')).toBeNull()
    expect(screen.queryByText('Update available')).toBeNull()
  })

  it('never infers Current from equal stale catalog and installed provenance', () => {
    renderWithProviders(
      <MarketplacePackageList
        installedPackages={[installedPackage()]}
        items={[packageItem({ state: 'stale' })]}
        onSelect={vi.fn()}
        selectedIdentifier={null}
      />
    )

    expect(screen.getByText('Installed v1.1.0')).toBeTruthy()
    expect(screen.getByText('Stale source')).toBeTruthy()
    expect(screen.queryByText('Current')).toBeNull()
    expect(screen.queryByText('Update available')).toBeNull()
    expect(api.inspect).not.toHaveBeenCalled()
  })

  it('distinguishes exact candidate identity from installed provenance without exposing actor data', () => {
    const installedCommit = 'c'.repeat(40)
    const installedDigest = 'd'.repeat(64)

    renderWithProviders(
      <MarketplacePackageDetail
        detail={packageDetail({
          installed: installedPackage({
            actor: '/private/tmp/operator-secret',
            configured_ref: 'release-1',
            distribution_digest: installedDigest,
            identity: { package_id: 'laptop-support', source_key: 'legacy-company' },
            installed_at: '2026-08-01T12:00:00Z',
            package_path: 'packages/installed-laptop-support',
            repository_url: 'https://installed.example.test/company/workflows.git',
            resolved_commit: installedCommit,
            source_name: 'legacy-company',
            version: '1.0.0'
          })
        })}
      />
    )

    const candidate = screen.getByRole('region', { name: 'Candidate package identity' })
    expect(within(candidate).getByText(DIGEST)).toBeTruthy()
    expect(within(candidate).getByText('packages/laptop-support')).toBeTruthy()

    const installed = screen.getByRole('region', { name: 'Installed package provenance' })
    expect(within(installed).getByText('legacy-company/laptop-support')).toBeTruthy()
    expect(within(installed).getByText('1.0.0')).toBeTruthy()
    expect(within(installed).getByText('legacy-company')).toBeTruthy()
    expect(
      within(installed).getByRole('link', { name: 'https://installed.example.test/company/workflows.git' })
    ).toBeTruthy()
    expect(within(installed).getByText('release-1')).toBeTruthy()
    expect(within(installed).getByText(installedCommit)).toBeTruthy()
    expect(within(installed).getByText(installedDigest)).toBeTruthy()
    expect(within(installed).getByText('2026-08-01T12:00:00Z')).toBeTruthy()
    expect(within(installed).getByText('packages/installed-laptop-support')).toBeTruthy()
    expect(screen.queryByText('/private/tmp/operator-secret')).toBeNull()
  })

  it('renders bounded decoded collections and never renders hidden raw content', () => {
    const items = Array.from({ length: 100 }, (_, index) =>
      packageItem({ display_name: `Package ${index}`, id: `package-${index}`, identifier: `company/package-${index}` })
    )

    renderWithProviders(
      <MarketplacePackageList installedPackages={[]} items={items} onSelect={vi.fn()} selectedIdentifier={null} />
    )

    expect(screen.getAllByRole('option')).toHaveLength(100)
    expect(screen.queryByText(/package_digest/i)).toBeNull()
    expect(screen.queryByText(DIGEST)).toBeNull()
  })
})

// Task 14C2 transitional gate makes these V1 mutation paths unreachable. Preserve their scenarios for the
// supervisor-backed adapter migration in 14D/E; the read-only structural-blocker assertion remains active.
describe('workflow package lifecycle', () => {
  async function selectPackage() {
    fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))
    await screen.findByRole('region', { name: 'Laptop Support package details' })
  }

  it.skip('installs only after review, invalidates origin truth, and opens trust as a separate fresh operation', async () => {
    const candidate = packageDetail({
      install_status: 'not_installed',
      installed: null,
      update_status: 'not_applicable'
    })

    api.inspect.mockResolvedValueOnce(succeededDetail(candidate))
    api.prepareInstall.mockResolvedValueOnce(
      lifecycleOperation('install_prepare', { type: 'install_review', value: installReview(candidate) })
    )
    api.confirmInstall.mockResolvedValueOnce(
      lifecycleOperation('install_confirm', {
        type: 'installed_package',
        value: installedPackage({ version: candidate.version })
      })
    )
    api.reviewTrust.mockResolvedValueOnce(
      lifecycleOperation('trust_prepare', { type: 'trust_review', value: trustReview() })
    )
    const rendered = renderMarketplace()
    const invalidate = vi.spyOn(rendered.client, 'invalidateQueries')

    await selectPackage()
    const action = screen.getByRole('button', { name: 'Install package' })
    fireEvent.click(action)
    fireEvent.click(action)

    expect(api.prepareInstall).toHaveBeenCalledTimes(1)
    expect(api.prepareInstall).toHaveBeenCalledWith({ identifier: 'company/laptop-support' }, scopeA)
    expect(await screen.findByRole('dialog', { name: 'Review installation' })).toBeTruthy()
    expect(globalThis.document.body.textContent).not.toContain(INSTALL_TOKEN)

    const confirm = screen.getByRole('button', { name: 'Confirm install' })
    fireEvent.click(confirm)
    fireEvent.click(confirm)

    await screen.findByText('Installed — trust required to run')
    expect(api.confirmInstall).toHaveBeenCalledTimes(1)
    expect(api.confirmInstall).toHaveBeenCalledWith(INSTALL_TOKEN, scopeA)
    expect(api.grantTrust).not.toHaveBeenCalled()
    expect(invalidate).toHaveBeenCalledWith({ queryKey: marketplaceKeys.installed('remote-a::support') })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: marketplaceKeys.searchRoot('remote-a::support') })
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: marketplaceKeys.detail('remote-a::support', 'company', 'laptop-support')
    })
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['workflow-catalog', 'remote-a::support'] })

    fireEvent.click(screen.getByRole('button', { name: 'Review trust' }))
    expect(await screen.findByRole('dialog', { name: 'Review trust' })).toBeTruthy()
    expect(api.reviewTrust).toHaveBeenCalledWith(
      { packageId: candidate.identity.package_id, sourceKey: candidate.identity.source_key },
      undefined,
      scopeA
    )
    expect(api.grantTrust).not.toHaveBeenCalled()
  })

  it.skip('shows prepare progress, cooperatively cancels, and stops old-scope polling without cross-publishing', async () => {
    const candidate = packageDetail({
      install_status: 'not_installed',
      installed: null,
      update_status: 'not_applicable'
    })

    const pending = pendingLifecycle('install_prepare')
    api.inspect.mockResolvedValue(succeededDetail(candidate))
    api.prepareInstall.mockResolvedValueOnce(pending)
    api.cancelOperation.mockResolvedValueOnce({
      ...pending,
      error: null,
      finished_at: NOW,
      phase: 'cancelled',
      progress: 10,
      started_at: NOW,
      state: 'cancelled'
    })
    const rendered = renderMarketplace()

    await selectPackage()
    vi.useFakeTimers()
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Install package' }))
      await Promise.resolve()
    })
    expect(screen.getByText('queued 0%')).toBeTruthy()
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Cancel operation' }))
      fireEvent.click(screen.getByRole('button', { name: 'Cancel operation' }))
      await Promise.resolve()
    })
    expect(api.cancelOperation).toHaveBeenCalledTimes(1)
    expect(api.cancelOperation).toHaveBeenCalledWith(OPERATION_ID, scopeA)
    expect(screen.getByText('Installation was cancelled. Nothing new was installed.')).toBeTruthy()

    api.prepareInstall.mockResolvedValueOnce(pending)
    fireEvent.click(screen.getByRole('button', { name: 'Prepare again' }))
    await act(async () => {
      rendered.rerender(
        <I18nProvider configClient={null} initialLocale="en">
          <QueryClientProvider client={rendered.client}>
            <WorkflowMarketplaceView scope={{ connectionId: 'remote-b', profile: 'support' }} />
          </QueryClientProvider>
        </I18nProvider>
      )
      await vi.advanceTimersByTimeAsync(500)
    })
    expect(screen.queryByRole('dialog', { name: 'Review installation' })).toBeNull()
  })

  it.skip('uses backend update truth for unchanged and preserves the exact previous version on failure', async () => {
    const current = packageDetail({ update_status: 'current' })
    api.inspect.mockResolvedValueOnce(succeededDetail(current))
    api.checkUpdates.mockResolvedValueOnce(
      lifecycleOperation('update_check', {
        type: 'update_checks',
        value: {
          checks: [
            {
              candidate_version: null,
              diagnostic_code: null,
              identity: current.identity,
              installed_version: '1.1.0',
              message: null,
              status: 'current'
            }
          ]
        }
      })
    )
    const rendered = renderMarketplace()
    await selectPackage()
    fireEvent.click(screen.getByRole('button', { name: 'Check for updates' }))
    expect(await screen.findByText('Version 1.1.0 is current. No update was installed.')).toBeTruthy()
    expect(api.prepareUpdate).not.toHaveBeenCalled()

    fireEvent.click(screen.getAllByRole('button', { name: 'Close' }).at(-1)!)
    api.inspect.mockResolvedValueOnce(succeededDetail(packageDetail()))
    rendered.client.removeQueries({
      queryKey: marketplaceKeys.detail('remote-a::support', 'company', 'laptop-support')
    })
    rendered.rerender(
      <I18nProvider configClient={null} initialLocale="en">
        <QueryClientProvider client={rendered.client}>
          <WorkflowMarketplaceView scope={scopeA} />
        </QueryClientProvider>
      </I18nProvider>
    )
    await selectPackage()
    api.prepareUpdate.mockResolvedValueOnce(
      lifecycleOperation('update_prepare', { type: 'update_review', value: updateReview() })
    )
    api.confirmUpdate.mockResolvedValueOnce(failedLifecycle('update_confirm'))
    fireEvent.click(screen.getByRole('button', { name: 'Update package' }))
    await screen.findByRole('dialog', { name: 'Review update' })
    fireEvent.click(screen.getByRole('button', { name: 'Confirm update' }))
    expect(await screen.findByText('Version 1.1.0 remains installed.')).toBeTruthy()
    expect(screen.queryByText(/rolled back/i)).toBeNull()
  })

  it.skip('uses an exact update check before preparing and leaves changed installed bytes untrusted', async () => {
    const current = packageDetail({ update_status: 'current' })
    api.inspect.mockResolvedValueOnce(succeededDetail(current))
    api.checkUpdates.mockResolvedValueOnce(
      lifecycleOperation('update_check', {
        type: 'update_checks',
        value: {
          checks: [
            {
              candidate_version: '1.1.0',
              diagnostic_code: null,
              identity: current.identity,
              installed_version: current.installed!.version,
              message: null,
              status: 'update_available'
            }
          ]
        }
      })
    )
    api.prepareUpdate.mockResolvedValueOnce(
      lifecycleOperation('update_prepare', { type: 'update_review', value: updateReview(current) })
    )
    api.confirmUpdate.mockResolvedValueOnce(
      lifecycleOperation('update_confirm', {
        type: 'updated_package',
        value: installedPackage({ version: current.version })
      })
    )
    renderMarketplace()

    await selectPackage()
    fireEvent.click(screen.getByRole('button', { name: 'Check for updates' }))
    expect(await screen.findByRole('dialog', { name: 'Review update' })).toBeTruthy()
    expect(api.checkUpdates).toHaveBeenCalledWith(
      { packageId: current.identity.package_id, sourceKey: current.identity.source_key },
      scopeA
    )
    expect(api.prepareUpdate).toHaveBeenCalledWith(
      { packageId: current.identity.package_id, sourceKey: current.identity.source_key },
      scopeA
    )

    fireEvent.click(screen.getByRole('button', { name: 'Confirm update' }))
    expect(await screen.findByText('Updated to version 1.1.0 — trust required to run')).toBeTruthy()
    expect(api.confirmUpdate).toHaveBeenCalledWith(UPDATE_TOKEN, scopeA)
    expect(api.grantTrust).not.toHaveBeenCalled()
  })

  it.skip('offers provenance-backed installed actions, removes only after review, and retains the package on failure', async () => {
    api.prepareRemove.mockResolvedValueOnce(
      lifecycleOperation('remove_prepare', { type: 'remove_review', value: removeReview() })
    )
    api.confirmRemove.mockResolvedValueOnce(failedLifecycle('remove_confirm'))
    renderWithProviders(
      <InstalledPackages scope={scopeA}>
        <div>Loose project workflow</div>
      </InstalledPackages>
    )

    const packageGroup = await screen.findByRole('article', { name: 'company/laptop-support installed package' })
    expect(within(packageGroup).getByRole('button', { name: 'Review trust' })).toBeTruthy()
    expect(within(packageGroup).getByRole('button', { name: 'Check for updates' })).toBeTruthy()
    expect(within(packageGroup).getByRole('button', { name: 'Remove package' })).toBeTruthy()
    expect(screen.getByText('Loose project workflow')).toBeTruthy()

    fireEvent.click(within(packageGroup).getByRole('button', { name: 'Remove package' }))
    expect(await screen.findByRole('dialog', { name: 'Review removal' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Remove package' }))
    expect(await screen.findByText('Version 1.1.0 remains installed.')).toBeTruthy()
    expect(api.confirmRemove).toHaveBeenCalledWith(REMOVE_TOKEN, scopeA)
  })

  it.skip('does not announce removal until the exact confirm operation reaches terminal success', async () => {
    const completion = deferred<WorkflowMarketplaceOperation>()
    api.prepareRemove.mockResolvedValueOnce(
      lifecycleOperation('remove_prepare', { type: 'remove_review', value: removeReview() })
    )
    api.confirmRemove.mockReturnValueOnce(completion.promise)
    renderWithProviders(
      <InstalledPackages scope={scopeA}>
        <div>Loose project workflow</div>
      </InstalledPackages>
    )

    const packageGroup = await screen.findByRole('article', { name: 'company/laptop-support installed package' })
    fireEvent.click(within(packageGroup).getByRole('button', { name: 'Remove package' }))
    await screen.findByRole('dialog', { name: 'Review removal' })
    fireEvent.click(screen.getByRole('button', { name: 'Remove package' }))
    expect(screen.queryByText('Package removed.')).toBeNull()
    expect(screen.getByText('Loose project workflow')).toBeTruthy()

    completion.resolve(lifecycleOperation('remove_confirm', { type: 'removed_package', value: installedPackage() }))
    expect(await screen.findByText('Package removed.')).toBeTruthy()
  })

  it.skip('re-prepares trust for exactly one selected workflow and grants only the fresh token', async () => {
    const allTrustReview = trustReview()
    allTrustReview.workflows.push({
      ...allTrustReview.workflows[0],
      definition_path: 'workflows/battery-diagnostic.yml',
      workflow_name: 'Battery diagnostic'
    })
    api.reviewTrust
      .mockResolvedValueOnce(lifecycleOperation('trust_prepare', { type: 'trust_review', value: allTrustReview }))
      .mockResolvedValueOnce(
        lifecycleOperation('trust_prepare', {
          type: 'trust_review',
          value: trustReview(installedPackage(), 'Laptop diagnostic')
        })
      )
    api.grantTrust.mockResolvedValueOnce(
      lifecycleOperation('trust_confirm', {
        type: 'trust_grant',
        value: { workflows: [{ state: 'trusted', workflow_name: 'Laptop diagnostic' }] }
      })
    )
    renderWithProviders(
      <InstalledPackages scope={scopeA}>
        <div />
      </InstalledPackages>
    )
    const packageGroup = await screen.findByRole('article', { name: 'company/laptop-support installed package' })
    fireEvent.click(within(packageGroup).getByRole('button', { name: 'Review trust' }))
    await screen.findByRole('dialog', { name: 'Review trust' })
    fireEvent.click(screen.getByRole('radio', { name: 'One workflow' }))

    await waitFor(() =>
      expect(api.reviewTrust).toHaveBeenLastCalledWith(
        {
          packageId: installedPackage().identity.package_id,
          sourceKey: installedPackage().identity.source_key
        },
        'Laptop diagnostic',
        scopeA
      )
    )
    expect(screen.getByRole('option', { name: 'Battery diagnostic' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Grant trust' }))
    await screen.findByText('Trust granted for the reviewed installed bytes.')
    expect(api.grantTrust).toHaveBeenCalledWith(`${TRUST_TOKEN}-one`, scopeA)
    expect(globalThis.document.body.textContent).not.toContain(TRUST_TOKEN)
  })

  it.skip('polls admitted preparation, stops on status failure, and retries only the exact operation', async () => {
    const candidate = packageDetail({
      install_status: 'not_installed',
      installed: null,
      update_status: 'not_applicable'
    })

    api.inspect.mockResolvedValueOnce(succeededDetail(candidate))
    api.prepareInstall.mockResolvedValueOnce(pendingLifecycle('install_prepare'))
    api.getOperation.mockRejectedValueOnce(
      new WorkflowMarketplaceApiError(
        'marketplace_network_error',
        0,
        'access_token=secret /private/tmp/operation-status'
      )
    )
    await renderMarketplace()
    await selectPackage()
    vi.useFakeTimers()

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Install package' }))
      await Promise.resolve()
      await vi.advanceTimersByTimeAsync(500)
    })

    expect(screen.getByText('Operation status is temporarily unavailable. Retry.')).toBeTruthy()
    expect(screen.queryByText(/access_token|private\/tmp/)).toBeNull()
    expect(api.getOperation).toHaveBeenCalledTimes(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_500)
    })
    expect(api.getOperation).toHaveBeenCalledTimes(1)

    api.getOperation.mockResolvedValueOnce(
      lifecycleOperation('install_prepare', { type: 'install_review', value: installReview(candidate) })
    )
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Retry status' }))
      await Promise.resolve()
    })

    expect(api.getOperation).toHaveBeenLastCalledWith(OPERATION_ID, scopeA)
    expect(screen.getByRole('dialog', { name: 'Review installation' })).toBeTruthy()
    expect(globalThis.document.body.textContent).not.toContain(INSTALL_TOKEN)
  })

  it.skip('stops an evicted operation without looping and admits one fresh replacement review', async () => {
    const candidate = packageDetail({
      install_status: 'not_installed',
      installed: null,
      update_status: 'not_applicable'
    })

    api.inspect.mockResolvedValueOnce(succeededDetail(candidate))
    api.prepareInstall
      .mockResolvedValueOnce(pendingLifecycle('install_prepare'))
      .mockResolvedValueOnce(
        lifecycleOperation('install_prepare', { type: 'install_review', value: installReview(candidate) })
      )
    api.getOperation.mockRejectedValueOnce(
      new WorkflowMarketplaceApiError('marketplace_operation_not_found', 404, 'private operation identity')
    )
    renderMarketplace()
    await selectPackage()
    vi.useFakeTimers()

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Install package' }))
      await Promise.resolve()
      await vi.advanceTimersByTimeAsync(500)
    })
    expect(screen.getByText('Operation status expired. Refresh package truth or prepare again.')).toBeTruthy()
    expect(api.getOperation).toHaveBeenCalledTimes(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_500)
      fireEvent.click(screen.getByRole('button', { name: 'Prepare again' }))
      await Promise.resolve()
    })
    expect(api.getOperation).toHaveBeenCalledTimes(1)
    expect(api.prepareInstall).toHaveBeenCalledTimes(2)
    expect(screen.getByRole('dialog', { name: 'Review installation' })).toBeTruthy()
  })

  it.skip('classifies rejected stale confirmation without installing or exposing backend details', async () => {
    const candidate = packageDetail({
      install_status: 'not_installed',
      installed: null,
      update_status: 'not_applicable'
    })

    api.inspect.mockResolvedValueOnce(succeededDetail(candidate))
    api.prepareInstall.mockResolvedValueOnce(
      lifecycleOperation('install_prepare', { type: 'install_review', value: installReview(candidate) })
    )
    api.confirmInstall.mockRejectedValueOnce(
      new WorkflowMarketplaceApiError(
        'confirmation_token_invalid',
        409,
        'access_token=secret /private/tmp/stale-review'
      )
    )
    renderMarketplace()

    await selectPackage()
    fireEvent.click(screen.getByRole('button', { name: 'Install package' }))
    await screen.findByRole('dialog', { name: 'Review installation' })
    fireEvent.click(screen.getByRole('button', { name: 'Confirm install' }))

    expect(
      await screen.findByText('The review expired or changed. Prepare a fresh review before continuing.')
    ).toBeTruthy()
    expect(screen.getByText('Nothing new was installed.')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Prepare again' })).toBeTruthy()
    expect(screen.queryByText(/access_token|private\/tmp/)).toBeNull()
    expect(api.grantTrust).not.toHaveBeenCalled()
  })

  it.skip('closes preparation without backend cancellation and returns focus to the originating action', async () => {
    const candidate = packageDetail({
      install_status: 'not_installed',
      installed: null,
      update_status: 'not_applicable'
    })

    api.inspect.mockResolvedValueOnce(succeededDetail(candidate))
    api.prepareInstall.mockResolvedValueOnce(pendingLifecycle('install_prepare'))
    renderMarketplace()

    await selectPackage()
    const action = screen.getByRole('button', { name: 'Install package' })
    action.focus()
    fireEvent.click(action)
    await screen.findByText('queued 0%')
    const dialog = screen.getByRole('dialog', { name: 'Preparing installation review' })
    fireEvent.click(within(dialog).getAllByRole('button', { name: 'Close' }).at(-1)!)

    await waitFor(() => expect(globalThis.document.activeElement).toBe(action))
    expect(api.cancelOperation).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it.skip('rejects a mismatched review identity and gates lifecycle controls on declared capabilities', async () => {
    const candidate = packageDetail({
      install_status: 'not_installed',
      installed: null,
      update_status: 'not_applicable'
    })

    api.inspect.mockResolvedValueOnce(succeededDetail(candidate))
    api.prepareInstall.mockResolvedValueOnce(
      lifecycleOperation('install_prepare', {
        type: 'install_review',
        value: installReview({ ...candidate, identity: { package_id: 'other-package', source_key: 'company' } })
      })
    )
    const rendered = renderMarketplace()

    await selectPackage()
    fireEvent.click(screen.getByRole('button', { name: 'Install package' }))
    expect(await screen.findByText('Installation failed. Nothing new was installed.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Confirm install' })).toBeNull()

    cleanup()
    api.capabilities.mockResolvedValueOnce({
      capabilities: ['sources', 'search', 'installed', 'operations'],
      profile: 'support',
      schema_version: 1
    })
    api.inspect.mockResolvedValueOnce(succeededDetail(candidate))
    rendered.client.clear()
    renderMarketplace()
    await selectPackage()
    expect(screen.queryByRole('button', { name: 'Install package' })).toBeNull()
    expect(screen.getByText('Upgrade Hermes to manage installed workflow packages.')).toBeTruthy()
  })

  it('does not offer installation for a freshly inspected candidate with structural blockers', async () => {
    const candidate = packageDetail({
      blockers: [{ code: 'package_invalid', message: 'Package structure is invalid.', severity: 'blocker' }],
      install_status: 'not_installed',
      installed: null,
      update_status: 'not_applicable'
    })

    api.inspect.mockResolvedValueOnce(succeededDetail(candidate))
    renderMarketplace()

    await selectPackage()
    expect(screen.getByText('Package structure is invalid.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Install package' })).toBeNull()
    expect(api.prepareInstall).not.toHaveBeenCalled()
  })

  it('retains last-known package detail when a lifecycle invalidation refetch fails', async () => {
    api.inspect
      .mockResolvedValueOnce(succeededDetail())
      .mockRejectedValueOnce(new WorkflowMarketplaceApiError('marketplace_network_error', 0, 'private refetch error'))
    const rendered = renderMarketplace()
    await selectPackage()
    expect(screen.getByRole('region', { name: 'Laptop Support package details' })).toBeTruthy()

    await rendered.client.invalidateQueries({
      queryKey: marketplaceKeys.detail('remote-a::support', 'company', 'laptop-support')
    })

    await waitFor(() => expect(api.inspect).toHaveBeenCalledTimes(2))
    expect(screen.getByRole('region', { name: 'Laptop Support package details' })).toBeTruthy()
    expect(screen.queryByText('Could not inspect workflow package')).toBeNull()
  })

  it.skip('discards a late lifecycle review when package selection changes', async () => {
    const lateReview = deferred<WorkflowMarketplaceOperation>()
    const first = packageDetail({ install_status: 'not_installed', installed: null, update_status: 'not_applicable' })

    const second = packageDetail({
      display_name: 'Printer Support',
      id: 'printer-support',
      identifier: 'company/printer-support',
      identity: { package_id: 'printer-support', source_key: 'company' },
      install_status: 'not_installed',
      installed: null,
      update_status: 'not_applicable'
    })

    api.search.mockResolvedValueOnce(
      page([
        packageItem(),
        packageItem({ display_name: 'Printer Support', id: 'printer-support', identifier: 'company/printer-support' })
      ])
    )
    api.inspect.mockImplementation((_source: string, packageId: string) =>
      Promise.resolve(succeededDetail(packageId === 'printer-support' ? second : first))
    )
    api.prepareInstall.mockReturnValueOnce(lateReview.promise)
    renderMarketplace()

    await selectPackage()
    fireEvent.click(screen.getByRole('button', { name: 'Install package' }))
    await screen.findByText('queued 0%')

    const replacement = globalThis.document.querySelector<HTMLButtonElement>(
      '[data-marketplace-package="company/printer-support"]'
    )!

    fireEvent.click(replacement)
    expect(await screen.findByRole('region', { name: 'Printer Support package details' })).toBeTruthy()

    lateReview.resolve(lifecycleOperation('install_prepare', { type: 'install_review', value: installReview(first) }))
    await act(async () => Promise.resolve())
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(globalThis.document.body.textContent).not.toContain(INSTALL_TOKEN)
  })
})

describe('supervised marketplace readiness and transitional adapters', () => {
  function Candidate({ detail, queryKey }: { detail: WorkflowMarketplacePackageDetail; queryKey: QueryKey }) {
    const truth = useMarketplaceReadOnlyScope(scopeA)

    return (
      <MarketplacePackageDetail
        detail={detail}
        lastObserved={truth.packageGate(detail.identity, queryKey)?.state !== 'ready'}
        lifecycleUnavailable
        packageState={truth.packageGate(detail.identity)?.packageState}
      />
    )
  }

  it.each(['detail', 'operation'] as const)('keeps wrong source %s metadata visibly last observed', async consumer => {
    const h = createLifecycleHarness()

    try {
      const binding = await h.bind()
      await h.mutate(binding, 'service update confirm')
      await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
      const detailKey = marketplaceKeys.detail(profileScopeKey(binding), 'other-source', 'laptop-support')
      const completed = legacyInspectionFixture('service inspect')

      if (completed.result?.type !== 'package_detail') {
        throw new Error('Expected genuine completed inspection')
      }

      const operationKey = marketplaceKeys.operation(profileScopeKey(binding), completed.id)
      await h.queryClient.fetchQuery({
        queryKey: detailKey,
        queryFn: async () => legacyInspectionFixture('service inspect pending')
      })
      await h.queryClient.fetchQuery({ queryKey: operationKey, queryFn: async () => completed })
      await h.queryClient.fetchQuery({ queryKey: detailKey, queryFn: async () => completed })
      renderLifecycleHarness(
        <Candidate detail={completed.result.value} queryKey={consumer === 'detail' ? detailKey : operationKey} />,
        h
      )
      expect(screen.getByText(/Last observed —/)).toBeTruthy()
      expect(screen.queryByRole('button', { name: 'Update package' })).toBeNull()
    } finally {
      cleanup()
      h.dispose()
    }
  })

  it('keeps the old pending inspection last-observed after mutation, failed replacement admission and remount', async () => {
    const h = createLifecycleHarness()

    try {
      const binding = await h.bind()
      const pending = legacyInspectionFixture('service inspect pending')
      const completed = legacyInspectionFixture('service inspect')
      api.inspect.mockResolvedValue(pending)
      api.getOperation.mockResolvedValue(completed)
      const view = renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
      await selectPackage()
      const region = screen.getByRole('region', { name: 'Laptop Support package details' })
      await waitFor(() => expect(within(region).queryByText(/Last observed —/)).toBeNull())
      api.inspect.mockRejectedValue(new Error('replacement inspection unavailable'))
      h.state('service installed A trusted')
      await act(async () => {
        await h.mutate(binding, 'service update confirm')
        await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
      })
      await waitFor(() => expect(api.inspect.mock.calls.length).toBeGreaterThan(1))
      expect(api.getOperation).toHaveBeenLastCalledWith(pending.id, scopeA)
      expect(within(region).getByText(/Last observed —/)).toBeTruthy()
      view.unmount()
      renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
      await selectPackage()
      expect(
        within(screen.getByRole('region', { name: 'Laptop Support package details' })).getByText(/Last observed —/)
      ).toBeTruthy()
      expect(screen.queryByRole('button', { name: 'Update package' })).toBeNull()
    } finally {
      cleanup()
      h.dispose()
    }
  })

  async function selectPackage() {
    fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))
    await screen.findByRole('region', { name: 'Laptop Support package details' })
  }

  it.each(['not_installed', 'update_available', 'current'] as const)(
    'cannot invoke supplied legacy callbacks from a direct %s detail render',
    async status => {
      const action = vi.fn()

      const detail = packageDetail(
        status === 'not_installed'
          ? { install_status: 'not_installed', installed: null, update_status: 'not_applicable' }
          : { update_status: status }
      )

      renderWithProviders(
        <MarketplacePackageDetail
          actions={{ install: action, update: action, remove: action, reviewTrust: action, checkForUpdates: action }}
          detail={detail}
        />
      )

      for (const button of screen.queryAllByRole('button')) {
        expect((button as HTMLButtonElement).disabled).toBe(true)
        fireEvent.click(button)
      }

      expect(action).not.toHaveBeenCalled()
      expect(screen.getByText(detail.description)).toBeTruthy()
    }
  )

  it('refreshes only locked local state and displays the complete new trust map, not old detail badges', async () => {
    const h = createLifecycleHarness()

    try {
      const binding = await h.bind()
      renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
      await selectPackage()
      api.inspect.mockRejectedValue(new Error('offline'))
      h.failOriginRefetches()
      await act(async () => {
        await h.mutate(binding, 'service update confirm')
      })
      h.state('service installed A trusted')
      const inspections = api.inspect.mock.calls.length
      fireEvent.click(screen.getByRole('button', { name: 'Refresh state' }))
      const trust = await screen.findByRole('region', { name: 'Current package trust' })
      expect(within(trust).getByText('A')).toBeTruthy()
      expect(within(trust).getByText('trusted')).toBeTruthy()
      expect(within(trust).getByText('B')).toBeTruthy()
      expect(within(trust).getByText('untrusted')).toBeTruthy()
      expect(api.inspect).toHaveBeenCalledTimes(inspections)
      expect(screen.getAllByText(/Last observed/).length).toBeGreaterThan(0)
    } finally {
      cleanup()
      h.dispose()
    }
  })

  it('does not invoke legacy lifecycle callbacks without an application supervisor', async () => {
    renderMarketplace()
    await selectPackage()

    for (const name of ['Install package', 'Update package', 'Remove package', 'Review trust']) {
      expect(screen.queryByRole('button', { name })).toBeNull()
    }

    expect(api.prepareInstall).not.toHaveBeenCalled()
    expect(api.prepareUpdate).not.toHaveBeenCalled()
    expect(api.prepareRemove).not.toHaveBeenCalled()
    expect(api.reviewTrust).not.toHaveBeenCalled()
  })

  it('keeps installed metadata readable without exposing unsupervised mutations', async () => {
    renderWithProviders(
      <InstalledPackages scope={scopeA}>
        <p>Loose workflows</p>
      </InstalledPackages>
    )
    expect((await screen.findByText('Loose workflows')).hidden).toBe(false)
    await screen.findByRole('article', { name: /company\/laptop-support/ })

    for (const name of ['Check for updates', 'Remove package', 'Review trust']) {
      expect(screen.queryByRole('button', { name })).toBeNull()
    }
  })

  it('labels a retained installed row last-observed after a locked read proves the package absent', async () => {
    const h = createLifecycleHarness()

    try {
      const binding = await h.bind()
      renderLifecycleHarness(
        <InstalledPackages scope={scopeA}>
          <p>Loose workflows</p>
        </InstalledPackages>,
        h
      )
      await screen.findByRole('article', { name: /company\/laptop-support/ })
      api.installed.mockRejectedValue(new Error('offline'))
      await act(async () => {
        await h.mutate(binding, 'service remove confirm')
        await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
      })
      expect(h.supervisor.getPackageGate(binding, lifecycleIdentity)).toMatchObject({
        state: 'ready',
        packageState: { state: 'absent' }
      })
      expect(screen.getByText(/Last observed/)).toBeTruthy()
      expect(screen.getByText(/Package is absent/)).toBeTruthy()
      expect(screen.queryByRole('button', { name: 'Remove package' })).toBeNull()
    } finally {
      cleanup()
      h.dispose()
    }
  })

  it('shows recovery-required truth instead of an installed or unchanged claim', async () => {
    const h = createLifecycleHarness()

    try {
      const binding = await h.bind()
      renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
      await selectPackage()
      h.failOriginRefetches()
      await act(async () => {
        await h.mutate(binding, 'service rollback failed')
      })
      expect(screen.getByText(/Recovery required/)).toBeTruthy()
      expect(screen.queryByRole('button', { name: 'Update package' })).toBeNull()
      expect(screen.queryByText(/remains installed/)).toBeNull()
    } finally {
      cleanup()
      h.dispose()
    }
  })

  it('reads orphaned installed provenance locally after source deletion without a Git detail dependency', async () => {
    const h = createLifecycleHarness()

    try {
      await h.bind()
      h.state('service installed A trusted', true)
      api.sources.mockResolvedValue({ sources: [], profile: 'support' })
      api.inspect.mockRejectedValue(new Error('source missing'))
      renderLifecycleHarness(
        <InstalledPackages scope={scopeA}>
          <p>Loose workflows</p>
        </InstalledPackages>,
        h
      )
      fireEvent.click(await screen.findByRole('button', { name: 'Refresh state' }))
      expect(await screen.findByText('Source unavailable')).toBeTruthy()
      expect(screen.queryByRole('button', { name: 'Remove package' })).toBeNull()
      expect(api.inspect).not.toHaveBeenCalled()
    } finally {
      cleanup()
      h.dispose()
    }
  })

  it.each(['service install confirm', 'service update confirm', 'service remove confirm', 'service grant one A'])(
    '%s retains explicit last-observed metadata after every refresh fails and the view reopens',
    async name => {
      const h = createLifecycleHarness()

      try {
        const binding = await h.bind()
        const view = renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
        await selectPackage()
        api.installed.mockRejectedValue(new Error('offline'))
        api.search.mockRejectedValue(new Error('offline'))
        api.inspect.mockRejectedValue(new Error('offline'))
        h.failOriginRefetches()
        await act(async () => {
          await h.mutate(binding, name)
          await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
        })
        expect(screen.getByText(/Refreshing package state/).hidden).toBe(false)
        expect(screen.getAllByText(/Last observed/).length).toBeGreaterThan(0)
        view.unmount()
        render(
          <h.Providers>
            <WorkflowMarketplaceView scope={scopeA} />
          </h.Providers>
        )
        await selectPackage()
        expect(screen.getByText(/Refreshing package state/).hidden).toBe(false)

        for (const action of ['Install package', 'Update package', 'Remove package', 'Review trust']) {
          expect(screen.queryByRole('button', { name: action })).toBeNull()
        }

        expect(api.confirmInstall).not.toHaveBeenCalled()
        expect(api.confirmUpdate).not.toHaveBeenCalled()
        expect(api.confirmRemove).not.toHaveBeenCalled()
        expect(api.grantTrust).not.toHaveBeenCalled()

        const keys = JSON.stringify(
          h.queryClient
            .getQueryCache()
            .getAll()
            .map(query => query.queryKey)
        )

        for (const privateValue of [
          'test-only-confirmation-material-1234567890',
          binding.principalBinding,
          binding.registryEpoch
        ]) {
          expect(keys).not.toContain(privateValue)
          expect(globalThis.document.body.innerHTML).not.toContain(privateValue)
        }
      } finally {
        cleanup()
        h.dispose()
      }
    }
  )
})
