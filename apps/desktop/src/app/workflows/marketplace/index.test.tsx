// @vitest-environment jsdom
import { QueryClient, QueryClientProvider, type QueryKey, useQuery } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { type ReactNode, useRef } from 'react'
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
  WorkflowMarketplaceUpdateReview
} from '@/types/hermes'

import { InstalledPackages } from './installed-packages'
import {
  createLifecycleHarness,
  legacyInspectionFixture,
  lifecycleFixture,
  lifecycleIdentity,
  lifecycleStateFixture,
  renderLifecycleHarness
} from './lifecycle-test-harness'
import { MarketplacePackageDetail } from './package-detail'
import { MarketplacePackageList } from './package-list'
import { marketplaceKeys } from './query-keys'
import { useMarketplaceReadOnlyScope } from './supervisor-provider'
import { SupervisedPackageActions } from './use-package-lifecycle'

import { WorkflowMarketplaceView } from './index'

const api = vi.hoisted(() => ({
  add: vi.fn(),
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
  addWorkflowMarketplaceSource: (...args: unknown[]) => api.add(...args),
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
const standaloneLifecycleCleanups: Array<() => void> = []

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

function ProjectionLifecycleProbe({
  enabled = true,
  staticQuery = false
}: {
  enabled?: boolean
  staticQuery?: boolean
}) {
  const truth = useMarketplaceReadOnlyScope(scopeA)
  const fallback = useRef<HTMLDivElement>(null)
  const key = marketplaceKeys.detail(profileScopeKey(scopeA), 'company', 'laptop-support')

  const query = useQuery<WorkflowMarketplaceOperation>({
    queryKey: key,
    queryFn: () => api.inspect('company', 'laptop-support', scopeA),
    enabled: enabled && !truth.quarantined,
    staleTime: staticQuery ? 'static' : 0,
    retry: false
  })

  const detail = query.data?.result?.type === 'package_detail' ? query.data.result.value : undefined

  return (
    <div ref={fallback}>
      {detail ? (
        <SupervisedPackageActions
          binding={truth.binding}
          detail={detail}
          focusFallbackRef={fallback}
          identity={lifecycleIdentity}
          projection={key}
          sourceName="company"
        />
      ) : null}
    </div>
  )
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
  api.add.mockReset().mockResolvedValue({ profile: 'support' })
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
  standaloneLifecycleCleanups.splice(0).forEach(dispose => dispose())
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

function standaloneLifecycleHarness() {
  const harness = createLifecycleHarness()
  standaloneLifecycleCleanups.push(harness.dispose)

  return harness
}

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
    const h = standaloneLifecycleHarness()
    await h.bind()
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)

    const manage = await screen.findByRole('button', { name: 'Manage Sources' })
    expect(screen.getByRole('button', { name: 'Refresh' })).toBeTruthy()
    manage.focus()
    fireEvent.click(manage)
    expect(await screen.findByRole('dialog', { name: 'Workflow sources' })).toBeTruthy()
    const dialog = screen.getByRole('dialog', { name: 'Workflow sources' })
    fireEvent.click(within(dialog).getAllByRole('button', { name: 'Close' })[0])
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Workflow sources' })).toBeNull())
    await waitFor(() =>
      expect(globalThis.document.activeElement).toBe(screen.getByRole('button', { name: 'Manage Sources' }))
    )
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
    const h = standaloneLifecycleHarness()
    await h.bind()
    h.route('refresh', 'service refresh')
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)

    fireEvent.click(await screen.findByRole('button', { name: 'Refresh' }))
    await waitFor(() =>
      expect(
        h.calls.filter(call => call.type === 'start' && call.input?.kind === 'refresh').map(call => call.input?.subject)
      ).toEqual([{ type: 'source', source_name: 'company' }])
    )
    expect(api.refreshSource).not.toHaveBeenCalled()
  })

  it.each([
    ['failed', 'Refresh status unavailable'],
    ['cancelled', 'Cancelled']
  ] as const)('keeps a %s toolbar refresh visible with a path to exact recovery', async (state, label) => {
    const h = standaloneLifecycleHarness()
    await h.bind()
    h.route('refresh', state === 'cancelled' ? 'service refresh cancelled' : 'service refresh failed committed')
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)

    fireEvent.click(await screen.findByRole('button', { name: 'Refresh' }))

    const results = await screen.findByRole('status', { name: 'Workflow source refresh results' })
    expect(within(results).getByText(`company: ${label}`)).toBeTruthy()
    expect(within(results).getByRole('button', { name: 'Manage Sources' })).toBeTruthy()
  })

  it('keeps an evicted toolbar refresh visible with a path to exact recovery', async () => {
    const h = standaloneLifecycleHarness()
    await h.bind()
    h.route('refresh', 'service refresh pending')
    h.failStatus('marketplace_operation_not_found', 404)
    h.evict()
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)

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
    const h = standaloneLifecycleHarness()
    await h.bind()
    const first = h.holdNextAdmission('refresh')
    h.route('refresh', 'service refresh')
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
    const view = renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)

    fireEvent.click(await screen.findByRole('button', { name: 'Refresh' }))
    await waitFor(() =>
      expect(h.calls.filter(call => call.type === 'start' && call.input?.kind === 'refresh')).toHaveLength(1)
    )

    await h.bind({ connectionId: 'remote-b', profile: 'support' })
    view.rerender(
      <h.Providers>
        <WorkflowMarketplaceView scope={{ connectionId: 'remote-b', profile: 'support' }} />
      </h.Providers>
    )

    const newScopeRefresh = await screen.findByRole('button', { name: 'Refresh' })
    expect(newScopeRefresh.getAttribute('aria-busy')).toBe('false')
    fireEvent.click(newScopeRefresh)
    await waitFor(() =>
      expect(
        h.calls.filter(call => call.type === 'start' && call.input?.kind === 'refresh').map(call => call.input?.subject)
      ).toEqual([
        { type: 'source', source_name: 'company' },
        { type: 'source', source_name: 'company' },
        { type: 'source', source_name: 'team' }
      ])
    )
    await act(async () => first.resolve())

    expect(h.calls.filter(call => call.type === 'start' && call.input?.kind === 'refresh')).toHaveLength(3)
    expect(api.refreshSource).not.toHaveBeenCalled()
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
    const h = standaloneLifecycleHarness()
    h.state('service installed A trusted')
    await h.bind()
    const inspection = lifecycleFixture('service inspect')

    if (inspection.result?.type !== 'package_detail') {
      throw new Error('Expected strict V2 package detail fixture')
    }

    h.routeValue('inspect', {
      ...inspection,
      result: { ...inspection.result, value: packageDetail() }
    })
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)

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
    expect(h.calls.filter(call => call.type === 'start' && call.input?.kind === 'inspect')).toHaveLength(1)
    expect(h.calls.find(call => call.input?.kind === 'inspect')?.input?.subject).toEqual({
      type: 'package',
      identity: lifecycleIdentity
    })
    expect(api.inspect).not.toHaveBeenCalled()
  })

  it('polls a bounded detail operation and never turns file, redacted, SSH, or SCP identities into links', async () => {
    const h = standaloneLifecycleHarness()
    await h.bind()
    h.route('inspect', 'service inspect pending')

    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))

    await waitFor(() => expect(h.calls.some(call => call.type === 'get')).toBe(true))
    h.complete('service inspect')
    expect(await screen.findByRole('region', { name: 'Laptop Support package details' })).toBeTruthy()
    expect(h.calls.filter(call => call.type === 'start' && call.input?.kind === 'inspect')).toHaveLength(1)
    expect(api.inspect).not.toHaveBeenCalled()
    expect(api.getOperation).not.toHaveBeenCalled()

    for (const repository_url of [
      'file:///REDACTED',
      'ssh://git@example.test/team/repo.git',
      'git@example.test:team/repo.git'
    ]) {
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
      const h = standaloneLifecycleHarness()
      await h.bind()
      h.route('inspect', state === 'cancelled' ? 'service inspect cancelled' : 'service read-only failure')

      renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
      fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))

      expect(await screen.findByText('Could not inspect this package')).toBeTruthy()
      expect(screen.queryByRole('status', { name: 'Loading workflow package details' })).toBeNull()
      expect(h.calls.some(call => call.type === 'get')).toBe(false)

      h.route('inspect', 'service inspect')
      fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
      expect(await screen.findByRole('region', { name: 'Laptop Support package details' })).toBeTruthy()
      expect(h.calls.filter(call => call.input?.kind === 'inspect')).toHaveLength(2)
      expect(api.inspect).not.toHaveBeenCalled()
    }
  )

  it('stops a rejected detail poll and starts one fresh exact retry without leaking its error', async () => {
    const h = standaloneLifecycleHarness()
    await h.bind()
    h.route('inspect', 'service inspect pending')
    h.failStatus('marketplace_network_error')

    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))

    expect(await screen.findByText('Could not inspect this package')).toBeTruthy()
    expect(screen.queryByText(/access_token|private\/tmp/)).toBeNull()
    expect(screen.queryByRole('status', { name: 'Loading workflow package details' })).toBeNull()
    const callsAfterFailure = h.calls.filter(call => call.type === 'get').length

    await new Promise(resolve => setTimeout(resolve, 600))
    expect(h.calls.filter(call => call.type === 'get')).toHaveLength(callsAfterFailure)

    h.route('inspect', 'service inspect')
    const retry = screen.getByRole('button', { name: 'Retry' }) as HTMLButtonElement
    fireEvent.click(retry)
    fireEvent.click(retry)

    expect(await screen.findByRole('region', { name: 'Laptop Support package details' })).toBeTruthy()
    expect(h.calls.filter(call => call.type === 'get')).toHaveLength(callsAfterFailure)
    const inspections = h.calls.filter(call => call.input?.kind === 'inspect')
    expect(inspections).toHaveLength(2)
    expect(inspections[1].input?.requestId).not.toBe(inspections[0].input?.requestId)
    expect(h.calls.some(call => call.type === 'cancel')).toBe(false)
    expect(api.inspect).not.toHaveBeenCalled()
  })

  it('serializes the automatic exact replacement when a pending inspection is evicted', async () => {
    const h = standaloneLifecycleHarness()
    await h.bind()
    h.route('inspect', 'service inspect pending')
    h.failStatus('marketplace_operation_not_found', 404)
    h.evict()

    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))
    await waitFor(() => expect(h.calls.filter(call => call.input?.kind === 'inspect')).toHaveLength(1))
    const replacement = h.holdNextAdmission('inspect')
    h.route('inspect', 'service inspect')

    await waitFor(() => expect(h.calls.filter(call => call.input?.kind === 'inspect')).toHaveLength(2))
    expect(screen.getByRole('status', { name: 'Loading workflow package details' })).toBeTruthy()
    await act(async () => replacement.resolve())

    expect(await screen.findByRole('region', { name: 'Laptop Support package details' })).toBeTruthy()
    const inspections = h.calls.filter(call => call.input?.kind === 'inspect')
    expect(inspections).toHaveLength(2)
    expect(inspections[1].input?.requestId).not.toBe(inspections[0].input?.requestId)
    expect(h.calls.some(call => call.type === 'cancel')).toBe(false)
    expect(api.inspect).not.toHaveBeenCalled()
  })

  it('re-enables an evicted-operation Retry after its replacement inspection fails', async () => {
    const h = standaloneLifecycleHarness()
    await h.bind()
    h.route('inspect', 'service inspect pending')
    h.failStatus('marketplace_operation_not_found', 404)
    h.evict()

    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))

    await waitFor(() => expect(h.calls.filter(call => call.input?.kind === 'inspect')).toHaveLength(1))
    h.routeRawOnce('inspect', 'service inspect wrong subject')
    await waitFor(() => expect(h.calls.filter(call => call.input?.kind === 'inspect')).toHaveLength(2))
    const retry = (await screen.findByRole('button', { name: 'Retry' })) as HTMLButtonElement
    await waitFor(() => expect(retry.disabled).toBe(false))
    h.route('inspect', 'service inspect')
    fireEvent.click(retry)

    expect(await screen.findByRole('region', { name: 'Laptop Support package details' })).toBeTruthy()
    expect(h.calls.filter(call => call.input?.kind === 'inspect')).toHaveLength(3)
    expect(api.inspect).not.toHaveBeenCalled()
  })

  it('does not let an in-flight replacement block recovery after the scope changes', async () => {
    const h = standaloneLifecycleHarness()
    await h.bind()
    h.route('inspect', 'service inspect pending')
    h.failStatus('marketplace_operation_not_found', 404)
    h.evict()

    const rendered = renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))
    const oldReplacement = h.holdNextAdmission('inspect')
    h.route('inspect', 'service inspect')
    fireEvent.click(await screen.findByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(h.calls.filter(call => call.input?.kind === 'inspect')).toHaveLength(2))

    const nextScope = { connectionId: 'remote-b', profile: 'support' }
    await h.bind(nextScope)
    rendered.rerender(
      <h.Providers>
        <WorkflowMarketplaceView scope={nextScope} />
      </h.Providers>
    )

    fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))
    expect(await screen.findByRole('region', { name: 'Laptop Support package details' })).toBeTruthy()
    await act(async () => oldReplacement.resolve())

    expect(screen.getByRole('region', { name: 'Laptop Support package details' })).toBeTruthy()
    expect(h.calls.filter(call => call.input?.kind === 'inspect')).toHaveLength(3)
    expect(h.capabilityCalls.some(call => call.connectionId === 'remote-b' && call.profile === 'support')).toBe(true)
    expect(api.inspect).not.toHaveBeenCalled()
  })

  it('sanitizes detail failures and retries a fresh inspection', async () => {
    const h = standaloneLifecycleHarness()
    await h.bind()
    h.routeRawOnce('inspect', 'service inspect wrong subject')

    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))

    expect(await screen.findByText('Could not inspect this package')).toBeTruthy()
    expect(screen.queryByText(/access_token|private\/tmp/)).toBeNull()
    h.route('inspect', 'service inspect')
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('region', { name: 'Laptop Support package details' })).toBeTruthy()
    expect(h.calls.filter(call => call.input?.kind === 'inspect')).toHaveLength(2)
    expect(api.inspect).not.toHaveBeenCalled()
  })

  it('clears selection and ignores an old delayed detail when connection and profile change', async () => {
    const h = standaloneLifecycleHarness()
    await h.bind()
    const oldDetail = h.holdNextAdmission('inspect')
    h.route('inspect', 'service inspect')
    api.search.mockImplementation((_query: string, scope: typeof scopeA) =>
      scope.connectionId === 'remote-a'
        ? Promise.resolve(page([packageItem()]))
        : Promise.resolve(
            page([packageItem({ display_name: 'New backend package', id: 'new', identifier: 'other/new' })])
          )
    )
    const rendered = renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    const oldOption = await screen.findByRole('option', { name: /Laptop Support/ })
    oldOption.focus()
    fireEvent.click(oldOption)
    expect(await screen.findByRole('status', { name: 'Loading workflow package details' })).toBeTruthy()

    const nextScope = { connectionId: 'remote-b', profile: 'other' }
    await h.bind(nextScope)
    rendered.rerender(
      <h.Providers>
        <WorkflowMarketplaceView scope={nextScope} />
      </h.Providers>
    )

    expect(await screen.findByText('New backend package')).toBeTruthy()
    await act(async () => oldDetail.resolve())
    expect(screen.queryByRole('region', { name: 'Laptop Support package details' })).toBeNull()
    expect(screen.queryByRole('option', { name: /Laptop Support/ })).toBeNull()
    await waitFor(() =>
      expect(window.document.activeElement).toBe(screen.getByRole('searchbox', { name: 'Search workflow packages' }))
    )
    expect(h.calls.some(call => call.type === 'cancel')).toBe(false)
    expect(api.inspect).not.toHaveBeenCalled()
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

// Application-supervisor lifecycle coverage, including every restored V1 migration-scenario intent.
describe('workflow package lifecycle', () => {
  const lifecycleCleanups: Array<() => void> = []
  afterEach(() => {
    lifecycleCleanups.splice(0).forEach(dispose => dispose())
    vi.unstubAllGlobals()
  })

  async function setupLifecycle(installed = false, clock?: Parameters<typeof createLifecycleHarness>[0]) {
    const h = createLifecycleHarness(clock)
    lifecycleCleanups.push(h.dispose)

    if (installed) {
      h.state('service installed untrusted')
    }

    h.route('install_prepare', 'service install prepare')
    h.route('install_confirm', 'service install confirm')
    h.route('update_check', 'service available update check')
    h.route('update_prepare', 'service update prepare')
    h.route('update_confirm', 'service update confirm')
    h.route('remove_prepare', 'service remove prepare')
    h.route('remove_confirm', 'service remove confirm')
    api.inspect.mockResolvedValue(legacyInspectionFixture('service inspect'))

    const tokens = vi.fn(async (input: { path: string }) => {
      const prepared = [...h.receipts.values()].find(value => input.path.endsWith('/' + value.id + '/review-token'))
      const review = prepared?.result?.value

      if (!prepared || !review || !('review_digest' in review) || !('expires_at' in review)) {
        throw new Error('Unexpected token request')
      }

      return {
        ok: true,
        value: {
          operation_id: prepared.id,
          request_id: prepared.request_id,
          subject: prepared.subject,
          selection: prepared.selection,
          review_digest: review.review_digest,
          expires_at: review.expires_at,
          confirmation_token: prepared.kind === 'trust_prepare' ? TRUST_TOKEN : INSTALL_TOKEN
        }
      }
    })

    vi.stubGlobal('hermesDesktop', { apiStructured: tokens })
    await h.bind()

    return Object.assign(h, { tokens })
  }

  async function selectPackage() {
    fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))
    await screen.findByRole('region', { name: 'Laptop Support package details' })
  }

  it('starts a fresh exact inspection after remount while the detached inspection remains supervised', async () => {
    const h = await setupLifecycle()
    h.route('inspect', 'service inspect pending')
    const first = renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)

    fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))
    await waitFor(() => expect(h.calls.filter(call => call.input?.kind === 'inspect')).toHaveLength(1))
    first.unmount()

    h.route('inspect', 'service inspect')
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))
    await screen.findByRole('region', { name: 'Laptop Support package details' })

    const inspections = h.calls.filter(call => call.type === 'start' && call.input?.kind === 'inspect')
    expect(inspections).toHaveLength(2)
    expect(inspections[1].input?.requestId).not.toBe(inspections[0].input?.requestId)
    expect(h.calls.some(call => call.type === 'cancel')).toBe(false)
  })

  function LockedTrustProbe() {
    const gate = useMarketplaceReadOnlyScope(scopeA).packageGate(lifecycleIdentity)
    const trust = gate?.packageState?.trust?.workflows.find(workflow => workflow.workflow_name === 'A')?.state

    return (
      <output aria-label="Locked workflow A trust">{gate ? `${gate.state}:${trust ?? 'none'}` : 'unavailable'}</output>
    )
  }

  it('keeps later-started mount reconciliation truth when the earlier package read settles last', async () => {
    const h = await setupLifecycle(true)
    const [earlier, later] = h.holdNextStateReads(2)
    const reconciliations = vi.spyOn(h.supervisor, 'reconcilePackage')
    api.installed.mockResolvedValue({
      packages: [lifecycleStateFixture('service installed untrusted').installed],
      profile: 'support'
    })

    renderLifecycleHarness(
      <>
        <InstalledPackages scope={scopeA}>
          <div />
        </InstalledPackages>
        <LockedTrustProbe />
      </>,
      h
    )

    await waitFor(() => expect(h.packageStateCalls()).toBe(2))
    await act(async () => {
      later.resolve(lifecycleStateFixture('service installed A trusted'))
      await reconciliations.mock.results[1].value
    })
    expect((await screen.findByLabelText('Locked workflow A trust')).textContent).toBe('ready:trusted')
    const packageGroup = await screen.findByRole('article', { name: 'company/laptop-support installed package' })
    const reviewTrust = within(packageGroup).getByRole('button', { name: 'Review trust' })
    expect(reviewTrust.hasAttribute('disabled')).toBe(false)

    await act(async () => {
      earlier.resolve(lifecycleStateFixture('service installed untrusted'))
      await reconciliations.mock.results[0].value
    })
    expect(screen.getByLabelText('Locked workflow A trust').textContent).toBe('ready:trusted')
    expect(reviewTrust.hasAttribute('disabled')).toBe(false)
  })

  it('keeps later-started trust-terminal truth when the lifecycle read settles last', async () => {
    const h = await setupLifecycle(true)
    h.state('service installed A trusted')
    h.route('trust_prepare', 'service review all')
    h.route('trust_confirm', 'service grant all')
    api.installed.mockResolvedValue({
      packages: [lifecycleStateFixture('service installed A trusted').installed],
      profile: 'support'
    })

    renderLifecycleHarness(
      <>
        <InstalledPackages scope={scopeA}>
          <div />
        </InstalledPackages>
        <LockedTrustProbe />
      </>,
      h
    )

    const packageGroup = await screen.findByRole('article', { name: 'company/laptop-support installed package' })
    const reviewTrust = within(packageGroup).getByRole('button', { name: 'Review trust' })
    await waitFor(() => expect(reviewTrust.hasAttribute('disabled')).toBe(false))
    fireEvent.click(reviewTrust)
    const dialog = await screen.findByRole('dialog', { name: 'Review trust' })
    const before = h.packageStateCalls()
    const [earlier, later] = h.holdNextStateReads(2)
    const reconciliations = vi.spyOn(h.supervisor, 'reconcilePackage')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Grant trust' }))
    await waitFor(() => expect(h.packageStateCalls()).toBe(before + 2))
    const terminal = lifecycleFixture('service grant all')

    if (!terminal.outcome || !('package_state' in terminal.outcome) || !terminal.outcome.package_state) {
      throw new Error('Expected generated grant-all PackageState')
    }

    const terminalState = terminal.outcome.package_state

    await act(async () => {
      later.resolve(terminalState)
      await reconciliations.mock.results[1].value
    })
    expect(await screen.findByText('Trust granted for all reviewed workflows.')).toBeTruthy()
    expect(within(screen.getByRole('region', { name: 'Current package trust' })).getAllByText('trusted')).toHaveLength(
      2
    )

    await act(async () => {
      earlier.resolve(lifecycleStateFixture('service installed A trusted'))
      await reconciliations.mock.results[0].value
    })
    expect(screen.getByText('Trust granted for all reviewed workflows.')).toBeTruthy()
    expect(screen.queryByText(/State could not be confirmed/)).toBeNull()
    expect(within(screen.getByRole('region', { name: 'Current package trust' })).getAllByText('trusted')).toHaveLength(
      2
    )
  })

  it('renews long-open ready authority and starts exactly one reviewed install with advancing clocks', async () => {
    let elapsed = 0

    const h = await setupLifecycle(false, () => ({
      wallNowMs: Date.parse('2026-09-05T12:00:00Z') + elapsed,
      monotonicNowMs: elapsed
    }))

    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    await selectPackage()
    expect((await screen.findByRole('button', { name: 'Install package' })).hasAttribute('disabled')).toBe(false)
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })

    try {
      await act(async () => {
        await h.bind()
      })
      await act(async () => {
        elapsed = 240000
        await vi.advanceTimersByTimeAsync(240000)
        elapsed = 300001
        await vi.advanceTimersByTimeAsync(60001)
      })
      const install = screen.getByRole('button', { name: 'Install package' })
      expect(install.hasAttribute('disabled')).toBe(false)
      await act(async () => {
        fireEvent.click(install)
        fireEvent.click(install)
      })
      expect(h.calls.filter(call => call.input?.kind === 'install_prepare')).toHaveLength(1)
    } finally {
      vi.useRealTimers()
    }

    expect(await screen.findByRole('dialog', { name: 'Review installation' })).not.toBeNull()
    expect(h.tokens).not.toHaveBeenCalled()
    expect(api.prepareInstall).not.toHaveBeenCalled()
  })

  it.each([
    ['Install package', false, 'install_prepare'],
    ['Check for updates', true, 'update_check'],
    ['Update package', true, 'update_check'],
    ['Remove package', true, 'remove_prepare']
  ] as const)(
    'recovers overdue authority for %s before starting the exact intent once',
    async (label, installed, kind) => {
      let elapsed = 0

      const h = await setupLifecycle(installed, () => ({
        wallNowMs: Date.parse('2026-09-05T12:00:00Z') + elapsed,
        monotonicNowMs: elapsed
      }))

      h.route('update_check', 'service update check')
      renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
      await selectPackage()
      const button = await screen.findByRole('button', { name: label })
      expect(button.hasAttribute('disabled')).toBe(false)
      const before = h.capabilityCalls.length
      const pending = h.holdCapabilities()
      elapsed = 300001
      fireEvent.click(button)
      fireEvent.click(button)
      await waitFor(() => expect(h.capabilityCalls).toHaveLength(before + 1))
      expect(h.calls.filter(call => call.type === 'start' && call.input?.kind !== 'inspect')).toHaveLength(0)
      expect(screen.getByText('Refreshing package state')).not.toBeNull()
      const concurrent = h.supervisor.reconcileScope(scopeA)
      pending.resolve()
      await concurrent
      await waitFor(() => expect(h.calls.filter(call => call.input?.kind === kind)).toHaveLength(1))
      expect(h.capabilityCalls).toHaveLength(before + 1)
      expect(h.capabilityCalls.at(-1)).toEqual({
        connectionId: 'remote-a',
        profile: 'support',
        connectionGeneration: 1
      })
      expect(h.tokens).not.toHaveBeenCalled()
    }
  )

  it('refreshes overdue confirm authority before requesting a token or committing', async () => {
    let elapsed = 0

    const h = await setupLifecycle(false, () => ({
      wallNowMs: Date.parse('2026-09-05T12:00:00Z') + elapsed,
      monotonicNowMs: elapsed
    }))

    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    await selectPackage()
    fireEvent.click(await screen.findByRole('button', { name: 'Install package' }))
    const confirm = await screen.findByRole('button', { name: 'Confirm install' })
    await waitFor(() => expect(confirm.hasAttribute('disabled')).toBe(false))
    const before = h.capabilityCalls.length
    const pending = h.holdCapabilities()
    elapsed = 300001
    fireEvent.click(confirm)
    fireEvent.click(confirm)
    await waitFor(() => expect(h.capabilityCalls).toHaveLength(before + 1))
    expect(h.tokens).not.toHaveBeenCalled()
    expect(h.calls.some(call => call.input?.kind === 'install_confirm')).toBe(false)
    pending.resolve()
    await waitFor(() => expect(h.calls.filter(call => call.input?.kind === 'install_confirm')).toHaveLength(1))
    expect(h.tokens).toHaveBeenCalledTimes(1)
  })

  it.each([
    ['Check for updates', 'update_check'],
    ['Update package', 'update_check'],
    ['Remove package', 'remove_prepare']
  ] as const)('retains an Installed-view overdue %s intent through temporary quarantine', async (label, kind) => {
    let elapsed = 0

    const h = await setupLifecycle(true, () => ({
      wallNowMs: Date.parse('2026-09-05T12:00:00Z') + elapsed,
      monotonicNowMs: elapsed
    }))

    h.route('update_check', 'service update check')
    api.installed.mockResolvedValue({
      profile: 'support',
      packages: [lifecycleStateFixture('service installed untrusted').installed]
    })

    const view = renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div>Loose workflows</div>
      </InstalledPackages>,
      h
    )

    const button = await screen.findByRole('button', { name: label })
    await waitFor(() => expect(button.hasAttribute('disabled')).toBe(false))
    const before = h.capabilityCalls.length
    const pending = h.holdCapabilities()
    elapsed = 300001
    fireEvent.click(button)
    fireEvent.click(button)
    await waitFor(() => expect(h.capabilityCalls).toHaveLength(before + 1))
    expect(screen.queryByRole('article')).toBeNull()
    expect(h.calls.filter(call => call.type === 'start' && call.input?.kind !== 'inspect')).toHaveLength(0)
    pending.resolve()
    await waitFor(() => expect(h.calls.filter(call => call.input?.kind === kind)).toHaveLength(1))
    expect(h.capabilityCalls).toHaveLength(before + 1)
    fireEvent.click((await screen.findAllByRole('button', { name: 'Close' }))[0])
    await waitFor(() => expect(globalThis.document.activeElement).toBe(view.container.firstElementChild))
  })

  describe.each(['Marketplace', 'Installed'] as const)('%s overdue recovery fencing', surface => {
    it.each([
      'partial',
      'updates unsupported',
      'generation',
      'principal',
      'epoch',
      'absent',
      'changed',
      'busy',
      'recovery',
      'probe failure',
      'state failure',
      'unmount',
      'navigation'
    ] as const)('abandons the exact pending check on %s', async change => {
      let elapsed = 0

      const h = await setupLifecycle(true, () => ({
        wallNowMs: Date.parse('2026-09-05T12:00:00Z') + elapsed,
        monotonicNowMs: elapsed
      }))

      h.route('update_check', 'service update check')
      api.installed.mockResolvedValue({
        profile: 'support',
        packages: [lifecycleStateFixture('service installed untrusted').installed]
      })

      const content = (scope: typeof scopeA) =>
        surface === 'Marketplace' ? (
          <WorkflowMarketplaceView scope={scope} />
        ) : (
          <InstalledPackages scope={scope}>
            <div>Loose workflows</div>
          </InstalledPackages>
        )

      const view = renderLifecycleHarness(content(scopeA), h)

      if (surface === 'Marketplace') {
        await selectPackage()
      }

      const action = await screen.findByRole('button', { name: 'Check for updates' })
      await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
      const before = h.capabilityCalls.length
      const pending = h.holdCapabilities()
      elapsed = 300001
      fireEvent.click(action)
      await waitFor(() => expect(h.capabilityCalls).toHaveLength(before + 1))
      const settled = h.supervisor.reconcileScope(scopeA)

      if (change === 'partial') {
        h.capabilities(['operations', 'package_state'])
      } else if (change === 'updates unsupported') {
        h.capabilities(['operations', 'admission_replay', 'package_state', 'transactions'])
      } else if (change === 'generation' || change === 'principal' || change === 'epoch') {
        h.changeAuthority(change)
      } else if (change === 'absent') {
        h.state('service absent')
      } else if (change === 'changed') {
        h.stateFromOutcome('service update confirm')
      } else if (change === 'busy') {
        h.state('service installed untrusted', false, true)
      } else if (change === 'recovery') {
        h.state('service recovery required')
      } else if (change === 'probe failure') {
        h.failCapabilities()
      } else if (change === 'state failure') {
        h.failOriginRefetches()
      } else if (change === 'unmount') {
        view.unmount()
      } else {
        view.rerender(<h.Providers>{content({ connectionId: 'remote-b', profile: 'support' })}</h.Providers>)
      }

      await act(async () => {
        pending.resolve()
        await settled
      })
      expect(h.calls.filter(call => call.type === 'start' && call.input?.kind !== 'inspect')).toHaveLength(0)
      expect(h.tokens).not.toHaveBeenCalled()
      expect(screen.queryByRole('dialog')).toBeNull()
    })
  })

  it('deduplicates an overdue proactive deadline with rapid action-time recovery', async () => {
    let elapsed = 0

    const h = await setupLifecycle(false, () => ({
      wallNowMs: Date.parse('2026-09-05T12:00:00Z') + elapsed,
      monotonicNowMs: elapsed
    }))

    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    await selectPackage()
    await screen.findByRole('button', { name: 'Install package' })
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })

    try {
      await act(async () => {
        await h.bind()
      })
      const before = h.capabilityCalls.length
      const pending = h.holdCapabilities()
      elapsed = 300001
      const button = screen.getByRole('button', { name: 'Install package' })
      await act(async () => {
        fireEvent.click(button)
        fireEvent.click(button)
        await vi.advanceTimersByTimeAsync(240000)
      })
      expect(h.capabilityCalls).toHaveLength(before + 1)
      expect(h.calls.filter(call => call.type === 'start' && call.input?.kind !== 'inspect')).toHaveLength(0)
      await act(async () => {
        pending.resolve()
        await vi.advanceTimersByTimeAsync(0)
      })
      expect(h.calls.filter(call => call.input?.kind === 'install_prepare')).toHaveLength(1)
      expect(h.capabilityCalls).toHaveLength(before + 1)
    } finally {
      h.dispose()
      vi.useRealTimers()
    }
  })

  it.each(['update', 'remove'] as const)(
    'recovers overdue Installed-view %s confirmation with the same reviewed package',
    async mode => {
      let elapsed = 0

      const h = await setupLifecycle(true, () => ({
        wallNowMs: Date.parse('2026-09-05T12:00:00Z') + elapsed,
        monotonicNowMs: elapsed
      }))

      api.installed.mockResolvedValue({
        profile: 'support',
        packages: [lifecycleStateFixture('service installed untrusted').installed]
      })
      renderLifecycleHarness(
        <InstalledPackages scope={scopeA}>
          <div>Loose workflows</div>
        </InstalledPackages>,
        h
      )
      fireEvent.click(
        await screen.findByRole('button', { name: mode === 'update' ? 'Update package' : 'Remove package' })
      )

      const confirm = await screen.findByRole('button', {
        name: mode === 'update' ? 'Confirm update' : 'Remove package'
      })

      await waitFor(() => expect(confirm.hasAttribute('disabled')).toBe(false))
      const before = h.capabilityCalls.length
      const pending = h.holdCapabilities()
      elapsed = 300001
      fireEvent.click(confirm)
      fireEvent.click(confirm)
      await waitFor(() => expect(h.capabilityCalls).toHaveLength(before + 1))
      expect(h.tokens).not.toHaveBeenCalled()
      pending.resolve()
      await waitFor(() => expect(h.calls.filter(call => call.input?.kind === mode + '_confirm')).toHaveLength(1))
      expect(h.tokens).toHaveBeenCalledTimes(1)
    }
  )

  it.each(['partial', 'changed', 'navigation', 'unmount'] as const)(
    'abandons overdue confirmation before token fetch on %s',
    async change => {
      let elapsed = 0

      const h = await setupLifecycle(false, () => ({
        wallNowMs: Date.parse('2026-09-05T12:00:00Z') + elapsed,
        monotonicNowMs: elapsed
      }))

      const view = renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
      await selectPackage()
      fireEvent.click(await screen.findByRole('button', { name: 'Install package' }))
      const confirm = await screen.findByRole('button', { name: 'Confirm install' })
      await waitFor(() => expect(confirm.hasAttribute('disabled')).toBe(false))
      const before = h.capabilityCalls.length
      const pending = h.holdCapabilities()
      elapsed = 300001
      fireEvent.click(confirm)
      await waitFor(() => expect(h.capabilityCalls).toHaveLength(before + 1))
      const settled = h.supervisor.reconcileScope(scopeA)

      if (change === 'partial') {
        h.capabilities(['operations'])
      } else if (change === 'changed') {
        h.state('service installed untrusted')
      } else if (change === 'navigation') {
        view.rerender(
          <h.Providers>
            <WorkflowMarketplaceView scope={{ connectionId: 'remote-b', profile: 'support' }} />
          </h.Providers>
        )
      } else {
        view.unmount()
      }

      await act(async () => {
        pending.resolve()
        await settled
      })
      expect(h.tokens).not.toHaveBeenCalled()
      expect(h.calls.some(call => call.input?.kind === 'install_confirm')).toBe(false)
    }
  )

  it('does not resume an overdue install when its selected inspection changes during recovery', async () => {
    let elapsed = 0

    const h = await setupLifecycle(false, () => ({
      wallNowMs: Date.parse('2026-09-05T12:00:00Z') + elapsed,
      monotonicNowMs: elapsed
    }))

    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    await selectPackage()
    const button = await screen.findByRole('button', { name: 'Install package' })
    const before = h.capabilityCalls.length
    const pending = h.holdCapabilities()
    elapsed = 300001
    fireEvent.click(button)
    await waitFor(() => expect(h.capabilityCalls).toHaveLength(before + 1))
    const settled = h.supervisor.reconcileScope(scopeA)

    const inspected = lifecycleFixture('service inspect')

    const changed = {
      ...inspected,
      subject: { type: 'package', identity: { package_id: 'other', source_key: 'company' } },
      result:
        inspected.result?.type === 'package_detail'
          ? {
              ...inspected.result,
              value: {
                ...inspected.result.value,
                id: 'other',
                identifier: 'company/other',
                identity: { package_id: 'other', source_key: 'company' }
              }
            }
          : inspected.result
    }

    await act(async () => {
      h.queryClient.setQueryData(marketplaceKeys.detail(profileScopeKey(scopeA), 'company', 'laptop-support'), changed)
    })
    await act(async () => {
      pending.resolve()
      await settled
    })
    expect(h.calls.filter(call => call.type === 'start' && call.input?.kind !== 'inspect')).toHaveLength(0)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  describe('exact V2 detail projection recovery', () => {
    it.each([
      ['Install package', false, 'install_prepare', null],
      ['Check for updates', true, 'update_check', null],
      ['Update package', true, 'update_check', null],
      ['Remove package', true, 'remove_prepare', null],
      ['Install package', false, 'install_confirm', 'Confirm install'],
      ['Update package', true, 'update_confirm', 'Confirm update'],
      ['Remove package', true, 'remove_confirm', 'Remove package']
    ] as const)(
      'waits for the exact held projection after PackageState before %s / %s / %s',
      async (label, installed, kind, confirmLabel) => {
        let elapsed = 0

        const h = await setupLifecycle(installed, () => ({
          wallNowMs: Date.parse('2026-09-05T12:00:00Z') + elapsed,
          monotonicNowMs: elapsed
        }))

        const binding = await h.bind()

        if (!confirmLabel) {
          h.route('update_check', 'service update check')
        }

        renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
        await selectPackage()
        let action = await screen.findByRole('button', { name: label })

        if (confirmLabel) {
          fireEvent.click(action)
          action = await screen.findByRole('button', { name: confirmLabel })
        }

        await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
        const stateCalls = vi.spyOn(h.supervisor, 'reconcilePackage')
        const state = h.holdState()
        const projection = h.holdNextAdmission('inspect')
        const key = marketplaceKeys.detail(profileScopeKey(scopeA), 'company', 'laptop-support')
        const before = h.calls.filter(call => call.input?.kind === 'inspect').length
        elapsed = 300001
        fireEvent.click(action)
        fireEvent.click(action)
        await waitFor(() => expect(stateCalls).toHaveBeenCalled())
        await act(async () => {
          state.resolve(lifecycleStateFixture(installed ? 'service installed untrusted' : 'service absent'))
          await Promise.all(stateCalls.mock.results.map(result => result.value))
        })
        await waitFor(() => expect(h.calls.filter(call => call.input?.kind === 'inspect')).toHaveLength(before + 1))
        expect(h.supervisor.getPackageGate(binding, lifecycleIdentity).state).toBe('ready')
        expect(h.supervisor.getPackageGate(binding, lifecycleIdentity, key).state).toBe('reconciling')
        expect(h.calls.filter(call => call.input?.kind === kind)).toHaveLength(0)
        expect(h.tokens).not.toHaveBeenCalled()
        await act(async () => {
          projection.resolve()
        })
        await waitFor(() => expect(h.calls.filter(call => call.input?.kind === kind)).toHaveLength(1))
        expect(h.tokens).toHaveBeenCalledTimes(confirmLabel ? 1 : 0)
      }
    )
  })

  it.each([
    'changed',
    'failed',
    'missing',
    'binding',
    'partial',
    'installed',
    'busy',
    'recovery',
    'state failure',
    'unmount',
    'navigation'
  ] as const)('abandons a held exact projection recovery on %s before POST or token retrieval', async failure => {
    let elapsed = 0

    const h = await setupLifecycle(false, () => ({
      wallNowMs: Date.parse('2026-09-05T12:00:00Z') + elapsed,
      monotonicNowMs: elapsed
    }))

    const view = renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    await selectPackage()
    const action = await screen.findByRole('button', { name: 'Install package' })
    const stateCalls = vi.spyOn(h.supervisor, 'reconcilePackage')
    const projection = h.holdNextAdmission('inspect')
    const key = marketplaceKeys.detail(profileScopeKey(scopeA), 'company', 'laptop-support')
    const before = h.calls.filter(call => call.input?.kind === 'inspect').length

    if (failure === 'failed' || failure === 'changed') {
      h.routeRawOnce('inspect', 'service inspect wrong subject')
    }

    elapsed = 300001
    fireEvent.click(action)
    await waitFor(() => expect(h.calls.filter(call => call.input?.kind === 'inspect')).toHaveLength(before + 1))
    await waitFor(() => expect(stateCalls).toHaveBeenCalled())
    await act(async () => {
      await Promise.all(stateCalls.mock.results.map(result => result.value))
    })

    if (failure === 'missing') {
      h.queryClient.removeQueries({ queryKey: key, exact: true })
    } else if (failure === 'binding') {
      h.changeAuthority('generation')
      await h.bind()
    } else if (failure === 'partial') {
      h.capabilities(['operations', 'package_state'])
      await h.bind()
    } else if (failure === 'installed') {
      h.state('service installed untrusted')
    } else if (failure === 'busy') {
      h.state('service absent', false, true)
    } else if (failure === 'recovery') {
      h.state('service recovery required')
    } else if (failure === 'state failure') {
      h.failOriginRefetches()
    } else if (failure === 'unmount') {
      view.unmount()
    } else if (failure === 'navigation') {
      view.rerender(
        <h.Providers>
          <WorkflowMarketplaceView scope={{ connectionId: 'remote-b', profile: 'support' }} />
        </h.Providers>
      )
    }

    await act(async () => {
      projection.resolve()
      await Promise.all(stateCalls.mock.results.map(result => result.value))
      expect(h.calls.filter(call => call.type === 'start' && call.input?.kind !== 'inspect')).toHaveLength(0)
      expect(h.tokens).not.toHaveBeenCalled()
    })
    expect(h.calls.filter(call => call.type === 'start' && call.input?.kind !== 'inspect')).toHaveLength(0)
    expect(h.tokens).not.toHaveBeenCalled()
  })

  it.each(['disabled', 'static'] as const)('does not authorize from a skipped %s exact projection', async kind => {
    let elapsed = 0

    const h = await setupLifecycle(false, () => ({
      wallNowMs: Date.parse('2026-09-05T12:00:00Z') + elapsed,
      monotonicNowMs: elapsed
    }))

    const view = renderLifecycleHarness(<ProjectionLifecycleProbe />, h)
    let action = await screen.findByRole('button', { name: 'Install package' })
    await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
    view.rerender(
      <h.Providers>
        <ProjectionLifecycleProbe enabled={kind !== 'disabled'} staticQuery={kind === 'static'} />
      </h.Providers>
    )
    action = await screen.findByRole('button', { name: 'Install package' })
    const before = h.capabilityCalls.length
    elapsed = 300001
    fireEvent.click(action)
    await act(async () => Promise.resolve())
    expect(h.capabilityCalls).toHaveLength(before)
    expect(h.calls.filter(call => call.type === 'start' && call.input?.kind !== 'inspect')).toHaveLength(0)
    expect(h.tokens).not.toHaveBeenCalled()
  })

  it('installs only after review, invalidates origin truth, and opens trust as a separate fresh operation', async () => {
    const h = await setupLifecycle()
    h.route('trust_prepare', 'service review all pending')
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)

    await selectPackage()
    const action = screen.getByRole('button', { name: 'Install package' })
    fireEvent.click(action)
    fireEvent.click(action)

    expect(await screen.findByRole('dialog', { name: 'Review installation' })).toBeTruthy()
    expect(globalThis.document.body.textContent).not.toContain(INSTALL_TOKEN)
    expect(h.calls.filter(call => call.input?.kind === 'install_prepare')).toHaveLength(1)

    const confirm = screen.getByRole('button', { name: 'Confirm install' })
    fireEvent.click(confirm)
    fireEvent.click(confirm)

    await screen.findByText('Installed — trust required to run')
    expect(h.calls.filter(call => call.input?.kind === 'install_confirm')).toHaveLength(1)
    expect(h.tokens).toHaveBeenCalledTimes(1)
    expect(api.grantTrust).not.toHaveBeenCalled()

    const reviewTrust = screen.getByRole('button', { name: 'Review trust' })
    await waitFor(() => expect(reviewTrust.hasAttribute('disabled')).toBe(false))
    fireEvent.click(reviewTrust)
    await waitFor(() => expect(h.calls.filter(call => call.input?.kind === 'trust_prepare')).toHaveLength(1))
    expect(await screen.findByRole('dialog', { name: 'Preparing trust review' })).toBeTruthy()
    expect(await screen.findByText('queued 0%')).toBeTruthy()
    expect(
      h.calls.filter(call => call.type === 'start' && call.input?.kind !== 'inspect').map(call => call.input?.kind)
    ).toEqual(['install_prepare', 'install_confirm', 'trust_prepare'])
    expect(h.tokens).toHaveBeenCalledTimes(1)
    expect(api.reviewTrust).not.toHaveBeenCalled()
    expect(api.grantTrust).not.toHaveBeenCalled()
  })

  it('renews overdue trust authority before preparing exactly once', async () => {
    let elapsed = 0

    const h = await setupLifecycle(true, () => ({
      wallNowMs: Date.parse('2026-09-05T12:00:00Z') + elapsed,
      monotonicNowMs: elapsed
    }))

    h.state('service installed A trusted')
    h.route('trust_prepare', 'service review all')
    api.installed.mockResolvedValue({
      packages: [lifecycleStateFixture('service installed A trusted').installed],
      profile: 'support'
    })
    renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div />
      </InstalledPackages>,
      h
    )
    const action = await screen.findByRole('button', { name: 'Review trust' })
    await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
    const before = h.capabilityCalls.length
    const pending = h.holdCapabilities()
    elapsed = 300001
    fireEvent.click(action)
    fireEvent.click(action)
    await waitFor(() => expect(h.capabilityCalls).toHaveLength(before + 1))
    expect(h.calls.filter(call => call.input?.kind === 'trust_prepare')).toHaveLength(0)
    expect(h.tokens).not.toHaveBeenCalled()
    pending.resolve()

    await waitFor(() => expect(h.calls.filter(call => call.input?.kind === 'trust_prepare')).toHaveLength(1))
    expect(await screen.findByRole('dialog', { name: 'Review trust' })).toBeTruthy()
  })

  it('renews overdue trust confirmation authority before token retrieval and admission', async () => {
    let elapsed = 0

    const h = await setupLifecycle(true, () => ({
      wallNowMs: Date.parse('2026-09-05T12:00:00Z') + elapsed,
      monotonicNowMs: elapsed
    }))

    h.state('service installed A trusted')
    h.route('trust_prepare', 'service review all')
    h.route('trust_confirm', 'service grant all')
    api.installed.mockResolvedValue({
      packages: [lifecycleStateFixture('service installed A trusted').installed],
      profile: 'support'
    })
    renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div />
      </InstalledPackages>,
      h
    )
    const action = await screen.findByRole('button', { name: 'Review trust' })
    await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
    fireEvent.click(action)
    const confirm = await screen.findByRole('button', { name: 'Grant trust' })
    const before = h.capabilityCalls.length
    const pending = h.holdCapabilities()
    elapsed = 300001
    fireEvent.click(confirm)
    fireEvent.click(confirm)
    await waitFor(() => expect(h.capabilityCalls).toHaveLength(before + 1))
    expect(h.tokens).not.toHaveBeenCalled()
    expect(h.calls.filter(call => call.input?.kind === 'trust_confirm')).toHaveLength(0)
    pending.resolve()

    expect(await screen.findByText('Trust granted for all reviewed workflows.')).toBeTruthy()
    expect(h.tokens).toHaveBeenCalledTimes(1)
    expect(h.calls.filter(call => call.input?.kind === 'trust_confirm')).toHaveLength(1)
  })

  it('waits for the exact held Marketplace detail projection before trust preparation', async () => {
    let elapsed = 0

    const h = await setupLifecycle(true, () => ({
      wallNowMs: Date.parse('2026-09-05T12:00:00Z') + elapsed,
      monotonicNowMs: elapsed
    }))

    h.state('service installed A trusted')
    h.route('trust_prepare', 'service review all')
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    await selectPackage()
    const action = await screen.findByRole('button', { name: 'Review trust' })
    await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
    const projection = h.holdNextAdmission('inspect')
    const before = h.calls.filter(call => call.input?.kind === 'inspect').length
    const capabilityBefore = h.capabilityCalls.length
    const capabilities = h.holdCapabilities()
    elapsed = 300001
    fireEvent.click(action)
    await waitFor(() => expect(h.capabilityCalls).toHaveLength(capabilityBefore + 1))
    expect(h.calls.filter(call => call.input?.kind === 'trust_prepare')).toHaveLength(0)
    capabilities.resolve()
    await waitFor(() => expect(h.calls.filter(call => call.input?.kind === 'inspect')).toHaveLength(before + 1))
    expect(h.calls.filter(call => call.input?.kind === 'trust_prepare')).toHaveLength(0)
    expect(h.tokens).not.toHaveBeenCalled()
    projection.resolve()

    await waitFor(() => expect(h.calls.filter(call => call.input?.kind === 'trust_prepare')).toHaveLength(1))
    expect(await screen.findByRole('dialog', { name: 'Review trust' })).toBeTruthy()
  })

  it.each(['partial', 'changed'] as const)(
    'abandons overdue trust preparation when renewed authority is %s',
    async change => {
      let elapsed = 0

      const h = await setupLifecycle(true, () => ({
        wallNowMs: Date.parse('2026-09-05T12:00:00Z') + elapsed,
        monotonicNowMs: elapsed
      }))

      h.state('service installed A trusted')
      h.route('trust_prepare', 'service review all')
      api.installed.mockResolvedValue({
        packages: [lifecycleStateFixture('service installed A trusted').installed],
        profile: 'support'
      })
      renderLifecycleHarness(
        <InstalledPackages scope={scopeA}>
          <div />
        </InstalledPackages>,
        h
      )
      const action = await screen.findByRole('button', { name: 'Review trust' })
      await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
      const pending = h.holdCapabilities()
      const before = h.capabilityCalls.length
      elapsed = 300001
      fireEvent.click(action)
      await waitFor(() => expect(h.capabilityCalls).toHaveLength(before + 1))
      const settled = h.supervisor.reconcileScope(scopeA)

      if (change === 'partial') {
        h.capabilities(['operations', 'admission_replay', 'package_state'])
      } else {
        h.stateFromOutcome('service update confirm')
      }

      await act(async () => {
        pending.resolve()
        await settled
      })
      expect(h.calls.filter(call => call.input?.kind === 'trust_prepare')).toHaveLength(0)
      expect(h.tokens).not.toHaveBeenCalled()
      expect(screen.queryByRole('dialog')).toBeNull()
    }
  )

  it('shows prepare progress, cooperatively cancels, and stops old-scope polling without cross-publishing', async () => {
    const h = await setupLifecycle()
    h.route('install_prepare', 'service install prepare pending')
    const view = renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    await selectPackage()
    fireEvent.click(await screen.findByRole('button', { name: 'Install package' }))
    await screen.findByText('queued 0%')
    fireEvent.click(await screen.findByRole('button', { name: 'Cancel operation' }))
    await screen.findByText('Cancelled before changes were committed.')
    expect(h.calls.filter(call => call.type === 'cancel').map(call => call.id)).toEqual([
      h.supervisor.$records.get().find(record => record.kind === 'install_prepare')?.operationId
    ])
    expect(screen.queryByText(/Nothing new was installed/)).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Prepare again' }))
    await screen.findByText('queued 0%')
    view.rerender(
      <h.Providers>
        <WorkflowMarketplaceView scope={{ connectionId: 'remote-b', profile: 'support' }} />
      </h.Providers>
    )
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(api.prepareInstall).not.toHaveBeenCalled()
  })

  it('uses backend update truth for unchanged and preserves the exact previous version on failure', async () => {
    const h = await setupLifecycle(true)
    h.route('update_check', 'service update check')
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    await selectPackage()
    fireEvent.click(screen.getByRole('button', { name: 'Check for updates' }))
    await screen.findByText('Version 1.0.0 is current. No update was installed.')
    expect(h.calls.filter(call => call.input?.kind === 'update_prepare')).toHaveLength(0)
    fireEvent.click(within(screen.getByRole('dialog')).getAllByRole('button', { name: 'Close' }).at(-1)!)
    h.route('update_check', 'service available update check')
    h.route('update_confirm', 'service verified rollback')
    fireEvent.click(screen.getByRole('button', { name: 'Update package' }))
    await screen.findByRole('dialog', { name: 'Review update' })
    expect(within(screen.getByRole('dialog')).getByText('1.0.0')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm update' }))
    await screen.findByText('Changes were rolled back. Currently installed: 2.0.0.')
    expect(screen.queryByText(/Version 1.1.0 remains installed/)).toBeNull()
  })

  it('uses an exact update check before preparing and leaves changed installed bytes untrusted', async () => {
    const h = await setupLifecycle(true)
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    await selectPackage()
    fireEvent.click(screen.getByRole('button', { name: 'Check for updates' }))
    await screen.findByRole('dialog', { name: 'Review update' })
    expect(
      h.calls.filter(call => call.type === 'start' && call.input?.kind !== 'inspect').map(call => call.input?.kind)
    ).toEqual(['update_check', 'update_prepare'])
    expect(h.calls.filter(call => call.input?.kind === 'update_check')[0].input?.body).toEqual({
      identity: lifecycleIdentity
    })
    expect(h.tokens).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm update' }))
    await screen.findByText(/Updated to version 2.0.0. Installation does not grant trust./)
    expect(h.tokens).toHaveBeenCalledTimes(1)
    const confirm = h.calls.find(call => call.input?.kind === 'update_confirm')!.input!
    const prepared = [...h.receipts.values()].find(value => value.kind === 'update_prepare')!
    expect(confirm.body).toMatchObject({
      confirmation_token: INSTALL_TOKEN,
      prepare_operation_id: prepared.id,
      subject: prepared.subject,
      selection: null
    })
    expect(JSON.stringify(h.supervisor.$records.get())).not.toContain(INSTALL_TOKEN)
    expect(
      JSON.stringify(
        h.queryClient
          .getQueryCache()
          .getAll()
          .map(query => [query.queryKey, query.state.data])
      )
    ).not.toContain(INSTALL_TOKEN)
    expect(globalThis.document.body.textContent).not.toContain(INSTALL_TOKEN)
    expect(api.confirmUpdate).not.toHaveBeenCalled()
    expect(api.grantTrust).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Grant trust' })).toBeNull()
  })

  it('offers provenance-backed installed actions, removes only after review, and retains the package on failure', async () => {
    const h = await setupLifecycle(true)
    h.route('remove_confirm', 'service remove confirm pending')
    renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div>Loose project workflow</div>
      </InstalledPackages>,
      h
    )
    const article = await screen.findByRole('article', { name: 'company/laptop-support installed package' })
    await waitFor(() =>
      expect(within(article).getByRole('button', { name: 'Remove package' }).hasAttribute('disabled')).toBe(false)
    )
    expect(within(article).getByRole('button', { name: 'Review trust' }).hasAttribute('disabled')).toBe(false)
    fireEvent.click(within(article).getByRole('button', { name: 'Remove package' }))
    const dialog = await screen.findByRole('dialog', { name: 'Review removal' })
    expect(within(dialog).getByText('2.0.0')).toBeTruthy()
    expect(h.tokens).not.toHaveBeenCalled()
    h.loseResponse('remove_confirm')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove package' }))
    await screen.findByText(/State could not be confirmed/)
    expect(screen.getByText('Loose project workflow')).toBeTruthy()
    expect(article.isConnected).toBe(true)
    expect(screen.queryByText(/remains installed|Package removal completed/)).toBeNull()
    expect(api.confirmRemove).not.toHaveBeenCalled()
  })

  it('grants all workflows from the real complete A/B trust result through the supervisor', async () => {
    const h = await setupLifecycle(true)
    h.route('trust_prepare', 'service review all')
    h.route('trust_confirm', 'service grant all')
    api.installed.mockResolvedValue({
      packages: [lifecycleStateFixture('service installed A trusted').installed],
      profile: 'support'
    })

    renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div>Loose workflow</div>
      </InstalledPackages>,
      h
    )

    const reviewTrust = await screen.findByRole('button', { name: 'Review trust' })
    await waitFor(() => expect(reviewTrust.hasAttribute('disabled')).toBe(false))
    fireEvent.click(reviewTrust)

    const review = await screen.findByRole('dialog', { name: 'Review trust' })
    expect(within(review).getByText('A')).toBeTruthy()
    expect(within(review).getByText('B')).toBeTruthy()
    expect(within(review).getByText('trusted')).toBeTruthy()
    expect(within(review).getByText('untrusted')).toBeTruthy()
    expect(h.tokens).not.toHaveBeenCalled()

    fireEvent.click(within(review).getByRole('button', { name: 'Grant trust' }))
    expect(await screen.findByText('Trust granted for all reviewed workflows.')).toBeTruthy()
    const current = screen.getByRole('region', { name: 'Current package trust' })
    expect(within(current).getByText('A')).toBeTruthy()
    expect(within(current).getByText('B')).toBeTruthy()
    expect(within(current).getAllByText('trusted')).toHaveLength(2)
    expect(h.calls.filter(call => call.type === 'start').map(call => call.input?.kind)).toEqual([
      'trust_prepare',
      'trust_confirm'
    ])
    expect(h.tokens).toHaveBeenCalledTimes(1)
    expect(api.reviewTrust).not.toHaveBeenCalled()
    expect(api.grantTrust).not.toHaveBeenCalled()
    expect(globalThis.document.body.textContent).not.toContain(TRUST_TOKEN)
  })

  it('re-prepares one selected workflow and preserves the non-selected trust state from the complete map', async () => {
    const h = await setupLifecycle(true)
    h.route('trust_prepare', 'service review all')
    h.route('trust_confirm', 'service grant one A')
    api.installed.mockResolvedValue({
      packages: [lifecycleStateFixture('service installed A trusted').installed],
      profile: 'support'
    })

    renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div />
      </InstalledPackages>,
      h
    )
    const action = await screen.findByRole('button', { name: 'Review trust' })
    await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
    fireEvent.click(action)
    await screen.findByRole('dialog', { name: 'Review trust' })
    h.route('trust_prepare', 'service review one A')
    const oneWorkflow = screen.getByRole('radio', { name: 'One workflow' })
    fireEvent.click(oneWorkflow)
    fireEvent.click(oneWorkflow)
    const selectedReview = await screen.findByRole('dialog', { name: 'Review trust' })
    await waitFor(() => expect(within(selectedReview).getAllByRole('article')).toHaveLength(1))
    expect(within(selectedReview).getByRole('option', { name: 'B' })).toBeTruthy()
    fireEvent.click(within(selectedReview).getByRole('button', { name: 'Grant trust' }))

    expect(await screen.findByText('Trust granted for A.')).toBeTruthy()
    const current = screen.getByRole('region', { name: 'Current package trust' })
    expect(within(current).getByText('A')).toBeTruthy()
    expect(within(current).getByText('trusted')).toBeTruthy()
    expect(within(current).getByText('B')).toBeTruthy()
    expect(within(current).getByText('untrusted')).toBeTruthy()
    expect(h.calls.filter(call => call.input?.kind === 'trust_prepare')).toHaveLength(2)
    expect(h.tokens).toHaveBeenCalledTimes(1)
  })

  it('does not announce removal until the exact confirm operation reaches terminal success', async () => {
    const h = await setupLifecycle(true)
    h.route('remove_confirm', 'service remove confirm pending')
    renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div>Loose project workflow</div>
      </InstalledPackages>,
      h
    )
    const article = await screen.findByRole('article', { name: 'company/laptop-support installed package' })
    await waitFor(() =>
      expect(within(article).getByRole('button', { name: 'Remove package' }).hasAttribute('disabled')).toBe(false)
    )
    fireEvent.click(within(article).getByRole('button', { name: 'Remove package' }))
    const dialog = await screen.findByRole('dialog', { name: 'Review removal' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove package' }))
    await screen.findByText('queued 0%')
    expect(screen.queryByText('Package removal completed.')).toBeNull()
    const confirm = [...h.receipts.values()].find(value => value.kind === 'remove_confirm')!
    await act(async () => {
      h.complete('service remove confirm')
      await new Promise(resolve => setTimeout(resolve, 550))
    })
    await screen.findByText('Package removal completed.')
    expect(h.calls.some(call => call.type === 'get' && call.id === confirm.id)).toBe(true)
    expect(screen.getByText('Loose project workflow')).toBeTruthy()
  })

  it('re-prepares trust for exactly one selected workflow and grants only the fresh token', async () => {
    const h = await setupLifecycle(true)
    h.route('trust_prepare', 'service review all')
    h.route('trust_confirm', 'service grant one A')
    api.installed.mockResolvedValue({
      packages: [lifecycleStateFixture('service installed A trusted').installed],
      profile: 'support'
    })
    renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div />
      </InstalledPackages>,
      h
    )
    const packageGroup = await screen.findByRole('article', { name: 'company/laptop-support installed package' })
    await waitFor(() =>
      expect(within(packageGroup).getByRole('button', { name: 'Review trust' }).hasAttribute('disabled')).toBe(false)
    )
    fireEvent.click(within(packageGroup).getByRole('button', { name: 'Review trust' }))
    await screen.findByRole('dialog', { name: 'Review trust' })
    h.route('trust_prepare', 'service review one A')
    fireEvent.click(screen.getByRole('radio', { name: 'One workflow' }))

    await waitFor(() => expect(h.calls.filter(call => call.input?.kind === 'trust_prepare')).toHaveLength(2))
    expect(screen.getByRole('option', { name: 'B' })).toBeTruthy()
    const grant = screen.getByRole('button', { name: 'Grant trust' })
    fireEvent.click(grant)
    fireEvent.click(grant)
    await screen.findByText('Trust granted for A.')
    expect(h.tokens).toHaveBeenCalledTimes(1)
    const confirm = h.calls.find(call => call.input?.kind === 'trust_confirm')!.input!

    const fresh = [...h.receipts.values()].find(
      value => value.kind === 'trust_prepare' && value.selection.type === 'one'
    )!

    expect(confirm.body).toMatchObject({
      confirmation_token: TRUST_TOKEN,
      prepare_operation_id: fresh.id,
      review_digest: fresh.result?.type === 'trust_review' ? fresh.result.value.review_digest : null,
      selection: { type: 'one', workflow_name: 'A' }
    })
    expect(api.reviewTrust).not.toHaveBeenCalled()
    expect(api.grantTrust).not.toHaveBeenCalled()
    expect(globalThis.document.body.textContent).not.toContain(TRUST_TOKEN)
  })

  it('rejects a stale all-workflow token after selection is re-prepared for one workflow', async () => {
    const h = await setupLifecycle(true)
    h.state('service installed A trusted')
    h.route('trust_prepare', 'service review all')
    api.installed.mockResolvedValue({
      packages: [lifecycleStateFixture('service installed A trusted').installed],
      profile: 'support'
    })
    renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div />
      </InstalledPackages>,
      h
    )
    const action = await screen.findByRole('button', { name: 'Review trust' })
    await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
    fireEvent.click(action)
    await screen.findByRole('dialog', { name: 'Review trust' })
    h.route('trust_prepare', 'service review one A')
    fireEvent.click(screen.getByRole('radio', { name: 'One workflow' }))
    await waitFor(() => expect(h.calls.filter(call => call.input?.kind === 'trust_prepare')).toHaveLength(2))
    const respond = h.tokens.getMockImplementation()!
    h.tokens.mockImplementationOnce(async input => {
      const response = await respond(input)

      return { ...response, value: { ...response.value, selection: { type: 'all' } } }
    })
    fireEvent.click(screen.getByRole('button', { name: 'Grant trust' }))

    expect(await screen.findByText('Trust was not granted. Prepare a fresh trust review.')).toBeTruthy()
    expect(h.calls.filter(call => call.input?.kind === 'trust_confirm')).toHaveLength(0)
    expect(h.tokens).toHaveBeenCalledTimes(1)
    expect(globalThis.document.body.textContent).not.toContain(TRUST_TOKEN)
  })

  it('keeps trust independently disabled when the exact binding lacks trust capability', async () => {
    const h = await setupLifecycle(true)
    h.capabilities(['operations', 'admission_replay', 'package_state', 'transactions', 'updates'])
    await h.bind()
    api.installed.mockResolvedValue({
      packages: [lifecycleStateFixture('service installed untrusted').installed],
      profile: 'support'
    })
    renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div />
      </InstalledPackages>,
      h
    )

    const action = await screen.findByRole('button', { name: 'Review trust' })
    expect(action.hasAttribute('disabled')).toBe(true)
    fireEvent.click(action)
    expect(h.calls.filter(call => call.input?.kind.startsWith('trust_'))).toHaveLength(0)
  })

  it('keeps the token ephemeral and does not admit a grant when the exact review token is unavailable', async () => {
    const h = await setupLifecycle(true)
    h.route('trust_prepare', 'service review all')
    api.installed.mockResolvedValue({
      packages: [lifecycleStateFixture('service installed A trusted').installed],
      profile: 'support'
    })
    renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div />
      </InstalledPackages>,
      h
    )
    const action = await screen.findByRole('button', { name: 'Review trust' })
    await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
    fireEvent.click(action)
    const dialog = await screen.findByRole('dialog', { name: 'Review trust' })
    h.tokens.mockRejectedValueOnce(new Error(`expired ${TRUST_TOKEN} /private/repository`))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Grant trust' }))

    expect(await screen.findByText('Trust was not granted. Prepare a fresh trust review.')).toBeTruthy()
    expect(h.calls.filter(call => call.input?.kind === 'trust_confirm')).toHaveLength(0)
    expect(JSON.stringify(h.supervisor.$records.get())).not.toContain(TRUST_TOKEN)
    expect(JSON.stringify(h.queryClient.getQueryCache().getAll())).not.toContain(TRUST_TOKEN)
    expect(globalThis.document.body.innerHTML).not.toContain(TRUST_TOKEN)
    expect(globalThis.document.body.textContent).not.toContain('/private/repository')
  })

  it('reports a lost trust preparation response as unconfirmed without claiming trust was not granted', async () => {
    const h = await setupLifecycle(true)
    h.route('trust_prepare', 'service review all')
    h.loseResponse('trust_prepare')
    api.installed.mockResolvedValue({
      packages: [lifecycleStateFixture('service installed A trusted').installed],
      profile: 'support'
    })
    renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div />
      </InstalledPackages>,
      h
    )
    const action = await screen.findByRole('button', { name: 'Review trust' })
    await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
    fireEvent.click(action)

    expect(
      await screen.findByText('State could not be confirmed. Refresh package state before trying again.')
    ).toBeTruthy()
    expect(screen.queryByText(/Trust was not granted/)).toBeNull()
    expect(screen.queryByText(/Trust granted/)).toBeNull()
  })

  it('keeps a possibly admitted trust grant fenced when its POST response is lost', async () => {
    const h = await setupLifecycle(true)
    h.route('trust_prepare', 'service review all')
    h.route('trust_confirm', 'service grant all')
    api.installed.mockResolvedValue({
      packages: [lifecycleStateFixture('service installed A trusted').installed],
      profile: 'support'
    })
    renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div />
      </InstalledPackages>,
      h
    )
    const action = await screen.findByRole('button', { name: 'Review trust' })
    await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
    fireEvent.click(action)
    const dialog = await screen.findByRole('dialog', { name: 'Review trust' })
    h.loseResponse('trust_confirm')
    h.failOriginRefetches()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Grant trust' }))

    expect(
      await screen.findByText('State could not be confirmed. Refresh package state before trying again.')
    ).toBeTruthy()
    expect(screen.queryByText(/Trust was not granted/)).toBeNull()
    expect(screen.queryByText(/Trust granted/)).toBeNull()
    const binding = await h.bind()
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity).state).not.toBe('ready')
    expect(h.supervisor.$records.get().find(value => value.kind === 'trust_confirm')?.barrier).toBe(true)
    const starts = h.calls.filter(call => call.input?.kind === 'trust_confirm')
    expect(starts).toHaveLength(1)
    expect([...h.receipts.values()].filter(value => value.kind === 'trust_confirm')).toHaveLength(1)
  })

  it('reports an evicted possibly admitted trust grant as unknown and retains its mutation barrier', async () => {
    const h = await setupLifecycle(true)
    h.route('trust_prepare', 'service review all')
    h.route('trust_confirm', 'service grant all pending')
    api.installed.mockResolvedValue({
      packages: [lifecycleStateFixture('service installed A trusted').installed],
      profile: 'support'
    })
    renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div />
      </InstalledPackages>,
      h
    )
    const action = await screen.findByRole('button', { name: 'Review trust' })
    await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
    fireEvent.click(action)
    const dialog = await screen.findByRole('dialog', { name: 'Review trust' })
    h.failStatus('marketplace_operation_not_found', 404)
    h.failOriginRefetches()
    h.evict()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Grant trust' }))

    expect(
      await screen.findByText('State could not be confirmed. Refresh package state before trying again.')
    ).toBeTruthy()
    expect(screen.queryByText(/Trust was not granted/)).toBeNull()
    expect(screen.queryByText(/Trust granted/)).toBeNull()
    const binding = await h.bind()
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity).state).toBe('unknown')
  })

  it('does not make stale trust actionable when terminal success cannot be reconciled', async () => {
    const h = await setupLifecycle(true)
    h.route('trust_prepare', 'service review all')
    h.route('trust_confirm', 'service grant all')
    api.installed.mockResolvedValue({
      packages: [lifecycleStateFixture('service installed A trusted').installed],
      profile: 'support'
    })
    renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div />
      </InstalledPackages>,
      h
    )
    const action = await screen.findByRole('button', { name: 'Review trust' })
    await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
    fireEvent.click(action)
    const dialog = await screen.findByRole('dialog', { name: 'Review trust' })
    h.failOriginRefetches()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Grant trust' }))

    expect(
      await screen.findByText('State could not be confirmed. Refresh package state before trying again.')
    ).toBeTruthy()
    expect(screen.queryByText(/Trust granted/)).toBeNull()
    expect(screen.queryByText(/Trust was not granted/)).toBeNull()
    expect(screen.getByText('company/laptop-support')).toBeTruthy()
    const binding = await h.bind()
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity).state).toBe('reconciling')
  })

  it('reports an evicted trust operation as unconfirmed without a rollback claim', async () => {
    const h = await setupLifecycle(true)
    h.route('trust_prepare', 'service review all pending')
    h.failStatus('marketplace_operation_not_found', 404)
    h.evict()
    api.installed.mockResolvedValue({
      packages: [lifecycleStateFixture('service installed A trusted').installed],
      profile: 'support'
    })
    renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div />
      </InstalledPackages>,
      h
    )
    const action = await screen.findByRole('button', { name: 'Review trust' })
    await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
    fireEvent.click(action)

    expect(
      await screen.findByText('State could not be confirmed. Refresh package state before trying again.')
    ).toBeTruthy()
    expect(screen.queryByText(/Trust was not granted/)).toBeNull()
    expect(screen.queryByText(/Trust granted/)).toBeNull()
  })

  it.each(['missing B', 'duplicate A', 'selected A untrusted', 'wrong digest'])(
    'fails closed on generated %s complete-map evidence after possible grant admission',
    async fixture => {
      const h = await setupLifecycle(true)
      h.route('trust_prepare', 'service review all')

      if (fixture === 'selected A untrusted') {
        const terminal = structuredClone(lifecycleFixture('service grant one A'))

        if (
          terminal.result?.type !== 'trust_grant' ||
          terminal.outcome?.type !== 'committed' ||
          !terminal.outcome.package_state?.trust
        ) {
          throw new Error('Expected generated one-workflow trust result')
        }

        terminal.result.value.workflows[0].state = 'untrusted'
        terminal.outcome.package_state.trust.workflows[0].state = 'untrusted'
        h.routeValue('trust_confirm', terminal)
      } else {
        h.routeRaw('trust_confirm', fixture)
      }

      api.installed.mockResolvedValue({
        packages: [lifecycleStateFixture('service installed A trusted').installed],
        profile: 'support'
      })
      renderLifecycleHarness(
        <InstalledPackages scope={scopeA}>
          <div />
        </InstalledPackages>,
        h
      )
      const action = await screen.findByRole('button', { name: 'Review trust' })
      await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
      fireEvent.click(action)
      await screen.findByRole('dialog', { name: 'Review trust' })
      h.route('trust_prepare', 'service review one A')
      fireEvent.click(screen.getByRole('radio', { name: 'One workflow' }))
      await waitFor(() => expect(h.calls.filter(call => call.input?.kind === 'trust_prepare')).toHaveLength(2))
      fireEvent.click(screen.getByRole('button', { name: 'Grant trust' }))

      expect(
        await screen.findByText('State could not be confirmed. Refresh package state before trying again.')
      ).toBeTruthy()
      expect(screen.queryByText(/Trust was not granted/)).toBeNull()
      expect(screen.queryByText(/Trust granted/)).toBeNull()
    }
  )

  it('accepts a reordered complete trust map by canonical member identity rather than array position', async () => {
    const h = await setupLifecycle(true)
    const terminal = structuredClone(lifecycleFixture('service grant all'))

    if (
      terminal.result?.type !== 'trust_grant' ||
      terminal.outcome?.type !== 'committed' ||
      !terminal.outcome.package_state?.trust
    ) {
      throw new Error('Expected generated all-workflow trust result')
    }

    terminal.result.value.workflows.reverse()
    terminal.outcome.package_state.trust.workflows.reverse()
    h.route('trust_prepare', 'service review all')
    h.routeValue('trust_confirm', terminal)
    api.installed.mockResolvedValue({
      packages: [lifecycleStateFixture('service installed A trusted').installed],
      profile: 'support'
    })
    renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div />
      </InstalledPackages>,
      h
    )
    const action = await screen.findByRole('button', { name: 'Review trust' })
    await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
    fireEvent.click(action)
    fireEvent.click(await screen.findByRole('button', { name: 'Grant trust' }))

    expect(await screen.findByText('Trust granted for all reviewed workflows.')).toBeTruthy()
    expect(within(screen.getByRole('region', { name: 'Current package trust' })).getAllByText('trusted')).toHaveLength(
      2
    )
  })

  it('rejects a generated one-workflow review correlated to the wrong requested selection', async () => {
    const h = await setupLifecycle(true)
    h.routeRaw('trust_prepare', 'service review one A wrong selection')
    api.installed.mockResolvedValue({
      packages: [lifecycleStateFixture('service installed untrusted').installed],
      profile: 'support'
    })
    renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div />
      </InstalledPackages>,
      h
    )
    const action = await screen.findByRole('button', { name: 'Review trust' })
    await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
    fireEvent.click(action)

    expect(
      await screen.findByText('State could not be confirmed. Refresh package state before trying again.')
    ).toBeTruthy()
    expect(h.tokens).not.toHaveBeenCalled()
    expect(h.calls.filter(call => call.input?.kind === 'trust_confirm')).toHaveLength(0)
  })

  it.each(['missing member', 'duplicate member', 'unknown member', 'wrong path'] as const)(
    'fails closed on a relationship-invalid all-workflow review with %s',
    async mutation => {
      const h = await setupLifecycle(true)
      const preparation = structuredClone(lifecycleFixture('service review all'))

      if (preparation.result?.type !== 'trust_review') {
        throw new Error('Expected generated all-workflow trust review')
      }

      const workflows = preparation.result.value.workflows

      if (mutation === 'missing member') {
        workflows.pop()
      } else if (mutation === 'duplicate member') {
        workflows[1] = { ...workflows[0] }
      } else if (mutation === 'unknown member') {
        workflows[1] = { ...workflows[1], workflow_name: 'C', definition_path: 'workflows/C.yaml' }
      } else {
        workflows[1] = { ...workflows[1], definition_path: 'workflows/renamed-B.yaml' }
      }

      h.routeValue('trust_prepare', preparation)
      api.installed.mockResolvedValue({
        packages: [lifecycleStateFixture('service installed A trusted').installed],
        profile: 'support'
      })
      renderLifecycleHarness(
        <InstalledPackages scope={scopeA}>
          <div />
        </InstalledPackages>,
        h
      )
      const action = await screen.findByRole('button', { name: 'Review trust' })
      await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
      fireEvent.click(action)

      expect(
        await screen.findByText('State could not be confirmed. Refresh package state before trying again.')
      ).toBeTruthy()
      expect(h.tokens).not.toHaveBeenCalled()
      expect(h.calls.filter(call => call.input?.kind === 'trust_confirm')).toHaveLength(0)
    }
  )

  it('rejects a valid complete terminal map whose canonical path no longer matches the reviewed inventory', async () => {
    const h = await setupLifecycle(true)
    const terminal = structuredClone(lifecycleFixture('service grant all'))

    if (
      terminal.result?.type !== 'trust_grant' ||
      terminal.outcome?.type !== 'committed' ||
      !terminal.outcome.package_state?.installed ||
      !terminal.outcome.package_state.trust
    ) {
      throw new Error('Expected generated all-workflow trust result')
    }

    terminal.result.value.workflows[1].definition_path = 'workflows/renamed-B.yaml'
    terminal.outcome.package_state.trust.workflows[1].definition_path = 'workflows/renamed-B.yaml'
    terminal.outcome.package_state.installed.workflow_paths[1] = 'workflows/renamed-B.yaml'
    h.route('trust_prepare', 'service review all')
    h.routeValue('trust_confirm', terminal)
    api.installed.mockResolvedValue({
      packages: [lifecycleStateFixture('service installed A trusted').installed],
      profile: 'support'
    })
    renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div />
      </InstalledPackages>,
      h
    )
    const action = await screen.findByRole('button', { name: 'Review trust' })
    await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
    fireEvent.click(action)
    fireEvent.click(await screen.findByRole('button', { name: 'Grant trust' }))

    expect(
      await screen.findByText('State could not be confirmed. Refresh package state before trying again.')
    ).toBeTruthy()
    expect(screen.queryByText(/Trust granted/)).toBeNull()
  })

  it('rejects terminal trust success when the locked PackageState has a different complete trust map', async () => {
    const h = await setupLifecycle(true)
    h.state('service installed A trusted')
    h.route('trust_prepare', 'service review all')
    h.route('trust_confirm', 'service grant all')
    api.installed.mockResolvedValue({
      packages: [lifecycleStateFixture('service installed A trusted').installed],
      profile: 'support'
    })
    renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div />
      </InstalledPackages>,
      h
    )
    const action = await screen.findByRole('button', { name: 'Review trust' })
    await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
    fireEvent.click(action)
    const dialog = await screen.findByRole('dialog', { name: 'Review trust' })
    h.onPost(() => h.state('service installed A trusted'), 'trust_confirm')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Grant trust' }))

    expect(
      await screen.findByText('State could not be confirmed. Refresh package state before trying again.')
    ).toBeTruthy()
    expect(screen.queryByText(/Trust granted/)).toBeNull()
  })

  it('discards a pending trust dialog and token-free attachment on profile navigation', async () => {
    const h = await setupLifecycle(true)
    h.route('trust_prepare', 'service review all pending')
    api.installed.mockResolvedValue({
      packages: [lifecycleStateFixture('service installed A trusted').installed],
      profile: 'support'
    })

    const view = renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div />
      </InstalledPackages>,
      h
    )

    const action = await screen.findByRole('button', { name: 'Review trust' })
    await waitFor(() => expect(action.hasAttribute('disabled')).toBe(false))
    fireEvent.click(action)
    await screen.findByText('queued 0%')
    view.rerender(
      <h.Providers>
        <InstalledPackages scope={{ connectionId: 'remote-b', profile: 'other' }}>
          <div />
        </InstalledPackages>
      </h.Providers>
    )
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(h.tokens).not.toHaveBeenCalled()
  })

  it('polls admitted preparation, stops on status failure, and retries only the exact operation', async () => {
    const h = await setupLifecycle()
    h.route('install_prepare', 'service install prepare pending')
    h.failStatus()
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    await selectPackage()
    fireEvent.click(screen.getByRole('button', { name: 'Install package' }))
    await screen.findByText(/State could not be confirmed/)
    const gets = h.calls.filter(call => call.type === 'get')
    expect(gets).toHaveLength(1)
    await act(async () => {
      await new Promise(resolve => setTimeout(resolve, 550))
    })
    expect(h.calls.filter(call => call.type === 'get')).toHaveLength(1)
    h.complete('service install prepare')
    fireEvent.click(screen.getByRole('button', { name: 'Retry status' }))
    await screen.findByRole('dialog', { name: 'Review installation' })
    expect(h.calls.filter(call => call.type === 'get').map(call => call.id)).toEqual([gets[0].id, gets[0].id])
    expect(h.calls.filter(call => call.type === 'start' && call.input?.kind !== 'inspect')).toHaveLength(1)
    expect(h.tokens).not.toHaveBeenCalled()
  })

  it('stops an evicted operation without looping and admits one fresh replacement review', async () => {
    const h = await setupLifecycle()
    h.route('install_prepare', 'service install prepare pending')
    h.failStatus('marketplace_operation_not_found', 404)
    h.evict()
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    await selectPackage()
    fireEvent.click(screen.getByRole('button', { name: 'Install package' }))
    await screen.findByText(/State could not be confirmed/)
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Prepare again' }).hasAttribute('disabled')).toBe(false)
    )
    const reads = h.calls.filter(call => call.type === 'get').length
    h.route('install_prepare', 'service install prepare')
    fireEvent.click(screen.getByRole('button', { name: 'Prepare again' }))
    await screen.findByRole('dialog', { name: 'Review installation' })
    expect(h.calls.filter(call => call.type === 'get')).toHaveLength(reads)

    const requests = h.calls
      .filter(call => call.type === 'start' && call.input?.kind !== 'inspect')
      .map(call => call.input?.requestId)

    expect(requests).toHaveLength(2)
    expect(new Set(requests).size).toBe(2)
  })

  it('classifies rejected stale confirmation without installing or exposing backend details', async () => {
    const h = await setupLifecycle()
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    await selectPackage()
    fireEvent.click(screen.getByRole('button', { name: 'Install package' }))
    await screen.findByRole('dialog', { name: 'Review installation' })
    h.tokens.mockRejectedValueOnce(new Error('access_token=secret /private/tmp/stale-review'))
    fireEvent.click(screen.getByRole('button', { name: 'Confirm install' }))
    await screen.findByText('Review is unavailable. Prepare a fresh review.')
    expect(screen.getByRole('button', { name: 'Prepare again' }).hasAttribute('disabled')).toBe(false)
    expect(screen.queryByText(/Nothing new was installed|access_token|private\/tmp/)).toBeNull()
    expect(h.calls.some(call => call.input?.kind === 'install_confirm')).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Prepare again' }))
    await screen.findByRole('dialog', { name: 'Review installation' })
    expect(h.tokens).toHaveBeenCalledTimes(1)
  })

  it('closes preparation without backend cancellation and returns focus to the originating action', async () => {
    const h = await setupLifecycle()
    h.route('install_prepare', 'service install prepare pending')
    const view = renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    await selectPackage()
    const action = screen.getByRole('button', { name: 'Install package' })
    fireEvent.click(action)
    await screen.findByText('queued 0%')
    fireEvent.click(within(screen.getByRole('dialog')).getAllByRole('button', { name: 'Close' }).at(-1)!)
    await waitFor(() =>
      expect(
        globalThis.document.activeElement === action ||
          globalThis.document.activeElement === screen.getByRole('searchbox')
      ).toBe(true)
    )
    expect(h.calls.filter(call => call.type === 'cancel')).toHaveLength(0)
    view.unmount()
    h.complete('service install prepare')
    await act(async () => {
      await new Promise(resolve => setTimeout(resolve, 550))
    })
    expect(h.supervisor.$records.get()[0].status).toBe('terminal')
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('rejects a mismatched review identity and gates lifecycle controls on declared capabilities', async () => {
    const h = await setupLifecycle()
    h.mismatch('install_prepare')
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    await selectPackage()
    fireEvent.click(screen.getByRole('button', { name: 'Install package' }))
    await screen.findByText(/State could not be confirmed/)
    expect(screen.queryByRole('button', { name: 'Confirm install' })).toBeNull()
    cleanup()
    h.dispose()
    const limited = await setupLifecycle()
    limited.capabilities(['operations', 'admission_replay', 'package_state', 'inspect'])
    await limited.bind()
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, limited)
    await selectPackage()
    expect(screen.getByRole('button', { name: 'Install package' }).hasAttribute('disabled')).toBe(true)
    expect(limited.calls.filter(call => call.type === 'start' && call.input?.kind !== 'inspect')).toHaveLength(0)
  })

  it('returns focus to the installed-view fallback when terminal removal removes its originating card', async () => {
    const h = await setupLifecycle(true)
    let removed = false
    api.installed.mockImplementation(async () => ({
      profile: 'support',
      packages: removed ? [] : [installedPackage()]
    }))
    h.onPost(() => {
      removed = [...h.receipts.values()].some(value => value.kind === 'remove_confirm')
    }, 'remove_confirm')
    renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <p>Loose workflows</p>
      </InstalledPackages>,
      h
    )
    const article = await screen.findByRole('article', { name: 'company/laptop-support installed package' })
    const fallback = screen.getByText('Loose workflows').parentElement
    const origin = await within(article).findByRole('button', { name: 'Remove package' })
    origin.focus()
    fireEvent.click(origin)
    const dialog = await screen.findByRole('dialog', { name: 'Review removal' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove package' }))
    await waitFor(() => expect(article.isConnected).toBe(false))
    await waitFor(() => expect(globalThis.document.activeElement).toBe(fallback))
    expect(h.calls.filter(call => call.type === 'cancel')).toHaveLength(0)
  })

  it('retains the exact returned admission key while its binding is temporarily suspended', async () => {
    const h = await setupLifecycle()
    h.route('install_prepare', 'service install prepare pending')
    h.onPost(() => h.disconnect(), 'install_prepare')
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    await selectPackage()
    fireEvent.click(screen.getByRole('button', { name: 'Install package' }))
    await screen.findByText(/State could not be confirmed/)
    expect(screen.queryByRole('button', { name: 'Confirm install' })).toBeNull()
    await act(async () => {
      await h.bind()
    })
    await screen.findByText('queued 0%')
    expect(h.calls.filter(call => call.type === 'start' && call.input?.kind !== 'inspect')).toHaveLength(1)
    expect(h.calls.filter(call => call.type === 'lookup').map(call => call.id)).toContain(
      h.calls.find(call => call.input?.kind === 'install_prepare')?.input?.requestId
    )
  })

  it('fences confirmation when the package disappears during the explicit token fetch', async () => {
    const h = await setupLifecycle(true)
    const binding = await h.bind()
    const tokenReply = deferred<void>()
    const respond = h.tokens.getMockImplementation()!
    h.tokens.mockImplementationOnce(async input => {
      const response = await respond(input)
      await tokenReply.promise

      return response
    })
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    await selectPackage()
    fireEvent.click(screen.getByRole('button', { name: 'Update package' }))
    await screen.findByRole('dialog', { name: 'Review update' })
    fireEvent.click(screen.getByRole('button', { name: 'Confirm update' }))
    await waitFor(() => expect(h.tokens).toHaveBeenCalledTimes(1))
    await act(async () => {
      h.state('service absent')
      await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
      tokenReply.resolve()
    })
    expect(h.calls.some(call => call.input?.kind === 'update_confirm')).toBe(false)
    expect(screen.getByRole('button', { name: 'Confirm update' }).hasAttribute('disabled')).toBe(true)
    expect(JSON.stringify(h.supervisor.$records.get())).not.toContain(INSTALL_TOKEN)
  })

  it.each(['service rollback failed', 'service recovery ambiguous', 'lost response', 'invalid terminal identity'])(
    '%s cannot claim rollback or a previous installed version',
    async scenario => {
      const h = await setupLifecycle(true)
      h.route('update_confirm', scenario.startsWith('service ') ? scenario : 'service update confirm pending')
      const view = renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
      await selectPackage()
      fireEvent.click(screen.getByRole('button', { name: 'Update package' }))
      await screen.findByRole('dialog', { name: 'Review update' })

      if (scenario === 'service rollback failed') {
        h.state('service recovery required')
      }

      if (scenario === 'service recovery ambiguous') {
        h.state('service ambiguous state')
      }

      if (scenario === 'lost response') {
        h.loseResponse('update_confirm')
      }

      if (scenario === 'invalid terminal identity') {
        h.mismatch('update_confirm')
      }

      fireEvent.click(screen.getByRole('button', { name: 'Confirm update' }))
      await screen.findByText(/State could not be confirmed/)
      expect(screen.queryByText(/remains installed|Nothing new was installed|Changes were rolled back/)).toBeNull()
      expect(screen.queryByRole('button', { name: 'Prepare again' })).toBeNull()
      view.unmount()
      renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
      await selectPackage()
      expect(screen.queryByRole('button', { name: 'Update package' })).toBeNull()
      expect(screen.queryByRole('dialog')).toBeNull()
      expect(JSON.stringify(h.supervisor.$records.get())).not.toContain(INSTALL_TOKEN)
    }
  )

  it('does not fetch a token or confirm an authoritative no-op update', async () => {
    const h = await setupLifecycle(true)
    h.route('update_prepare', 'service unchanged update')
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    await selectPackage()
    fireEvent.click(screen.getByRole('button', { name: 'Update package' }))
    await screen.findByText('Version 1.0.0 is current. No update was installed.')
    expect(h.tokens).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Confirm update' })).toBeNull()
    expect(h.calls.some(call => call.input?.kind === 'update_confirm')).toBe(false)
  })

  it('discards a token returned after navigation without admitting confirmation', async () => {
    const h = await setupLifecycle()
    const tokenReply = deferred<void>()
    const respond = h.tokens.getMockImplementation()!
    h.tokens.mockImplementationOnce(async input => {
      const response = await respond(input)
      await tokenReply.promise

      return response
    })
    const view = renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    await selectPackage()
    fireEvent.click(screen.getByRole('button', { name: 'Install package' }))
    await screen.findByRole('dialog', { name: 'Review installation' })
    fireEvent.click(screen.getByRole('button', { name: 'Confirm install' }))
    await waitFor(() => expect(h.tokens).toHaveBeenCalledTimes(1))
    view.unmount()
    await act(async () => tokenReply.resolve())
    expect(h.calls.some(call => call.input?.kind === 'install_confirm')).toBe(false)
    expect(JSON.stringify(h.supervisor.$records.get())).not.toContain(INSTALL_TOKEN)
    expect(globalThis.document.body.textContent).not.toContain(INSTALL_TOKEN)
  })

  it('rejects an expired or mismatched token endpoint response and requires a fresh review', async () => {
    const h = await setupLifecycle()
    const respond = h.tokens.getMockImplementation()!
    h.tokens.mockImplementationOnce(async input => {
      const response = await respond(input)

      return { ...response, value: { ...response.value, expires_at: '2026-09-05T12:00:00Z' } }
    })
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    await selectPackage()
    fireEvent.click(screen.getByRole('button', { name: 'Install package' }))
    await screen.findByRole('dialog', { name: 'Review installation' })
    fireEvent.click(screen.getByRole('button', { name: 'Confirm install' }))
    await screen.findByText('Review is unavailable. Prepare a fresh review.')
    expect(h.calls.some(call => call.input?.kind === 'install_confirm')).toBe(false)
    expect(screen.queryByText(/Nothing new was installed/)).toBeNull()
  })

  it('renders a real direct-package check error without preparing or claiming the package is current', async () => {
    const h = await setupLifecycle()
    h.state('service direct installed')
    const state = lifecycleStateFixture('service direct installed')
    api.installed.mockResolvedValue({ profile: 'support', packages: [state.installed] })
    h.route('update_check', 'service failed update check')
    renderLifecycleHarness(
      <InstalledPackages scope={scopeA}>
        <div>Loose workflows</div>
      </InstalledPackages>,
      h
    )
    const check = await screen.findByRole('button', { name: 'Check for updates' })
    fireEvent.click(check)
    await screen.findByText('Could not check for updates.')
    expect(h.calls.find(call => call.input?.kind === 'update_check')?.input?.body).toEqual({ identity: state.identity })
    expect(screen.queryByText(/is current|No update was installed|remains installed/)).toBeNull()
    expect(h.calls.some(call => call.input?.kind === 'update_prepare')).toBe(false)
    expect(screen.queryByRole('button', { name: 'Prepare again' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Retry status' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Retry check' }))
    await waitFor(() => expect(h.calls.filter(call => call.input?.kind === 'update_check')).toHaveLength(2))
    const checks = h.calls.filter(call => call.input?.kind === 'update_check').map(call => call.input!)
    expect(checks[1].requestId).not.toBe(checks[0].requestId)
    expect(checks[1].body).toEqual({ identity: state.identity })
    expect(h.tokens).not.toHaveBeenCalled()
    expect(api.inspect).not.toHaveBeenCalled()
  })

  it('does not offer installation for a freshly inspected candidate with structural blockers', async () => {
    const h = await setupLifecycle()
    const inspection = lifecycleFixture('service inspect')

    if (inspection.result?.type !== 'package_detail') {
      throw new Error('Expected generated inspection detail')
    }

    h.routeValue('inspect', {
      ...inspection,
      result: {
        ...inspection.result,
        value: {
          ...inspection.result.value,
          blockers: [{ code: 'package_invalid', message: 'Package structure is invalid.', severity: 'blocker' }]
        }
      }
    })
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)

    await selectPackage()
    expect(screen.getByText('Package structure is invalid.')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Install package' })).toBeNull()
    expect(h.calls.some(call => call.input?.kind === 'install_prepare')).toBe(false)
  })

  it('retains last-known package detail when a lifecycle invalidation refetch fails', async () => {
    const h = await setupLifecycle()
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    await selectPackage()
    expect(screen.getByRole('region', { name: 'Laptop Support package details' })).toBeTruthy()
    const before = h.calls.filter(call => call.input?.kind === 'inspect').length
    h.routeRawOnce('inspect', 'service inspect wrong subject')

    await h.queryClient.invalidateQueries({
      queryKey: marketplaceKeys.detail('remote-a::support', 'company', 'laptop-support')
    })

    await waitFor(() => expect(h.calls.filter(call => call.input?.kind === 'inspect')).toHaveLength(before + 1))
    expect(screen.getByRole('region', { name: 'Laptop Support package details' })).toBeTruthy()
    expect(screen.queryByText('Could not inspect workflow package')).toBeNull()
  })

  it('discards a late lifecycle review when package selection changes', async () => {
    const h = await setupLifecycle()
    const admission = h.holdAdmission('install_prepare')
    api.search.mockResolvedValue(
      page([
        packageItem(),
        packageItem({ display_name: 'Printer Support', id: 'printer-support', identifier: 'company/printer-support' })
      ])
    )
    renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
    await selectPackage()
    fireEvent.click(screen.getByRole('button', { name: 'Install package' }))
    await screen.findByText('queued 0%')
    expect(screen.queryByRole('button', { name: 'Cancel operation' })).toBeNull()
    fireEvent.click(within(screen.getByRole('dialog')).getAllByRole('button', { name: 'Close' }).at(-1)!)
    const printer = lifecycleFixture('service inspect')

    if (printer.result?.type !== 'package_detail') {
      throw new Error('Expected generated inspection detail')
    }

    h.routeValue('inspect', {
      ...printer,
      subject: { type: 'package', identity: { package_id: 'printer-support', source_key: 'company' } },
      result: {
        ...printer.result,
        value: {
          ...printer.result.value,
          display_name: 'Printer Support',
          id: 'printer-support',
          identifier: 'company/printer-support',
          identity: { package_id: 'printer-support', source_key: 'company' }
        }
      }
    })
    fireEvent.click(screen.getByRole('option', { name: /Printer Support/ }))
    await screen.findByRole('region', { name: 'Printer Support package details' })
    await act(async () => admission.resolve())
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(h.tokens).not.toHaveBeenCalled()
    expect(h.supervisor.$records.get()[0].status).toBe('terminal')
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

  it('keeps the last accepted inspection last-observed after mutation, failed replacement and remount', async () => {
    const h = createLifecycleHarness()

    try {
      const binding = await h.bind()
      h.route('inspect', 'service inspect')
      const view = renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
      await selectPackage()
      const region = screen.getByRole('region', { name: 'Laptop Support package details' })
      await waitFor(() => expect(within(region).queryByText(/Last observed —/)).toBeNull())
      const inspections = h.calls.filter(call => call.type === 'start' && call.input?.kind === 'inspect').length
      h.routeRawOnce('inspect', 'service inspect wrong subject')
      h.state('service installed A trusted')
      await act(async () => {
        await h.mutate(binding, 'service update confirm')
        await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
      })
      await waitFor(() =>
        expect(h.calls.filter(call => call.type === 'start' && call.input?.kind === 'inspect').length).toBeGreaterThan(
          inspections
        )
      )
      expect(within(region).getByText(/Last observed —/)).toBeTruthy()
      view.unmount()
      h.routeRawOnce('inspect', 'service inspect wrong subject')
      renderLifecycleHarness(<WorkflowMarketplaceView scope={scopeA} />, h)
      await selectPackage()
      expect(
        within(screen.getByRole('region', { name: 'Laptop Support package details' })).getByText(/Last observed —/)
      ).toBeTruthy()
      expect(screen.queryByRole('button', { name: 'Update package' })).toBeNull()
      expect(api.inspect).not.toHaveBeenCalled()
      expect(api.getOperation).not.toHaveBeenCalled()
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

  it('keeps V1-only catalog and source CRUD usable without unsupervised inspection or lifecycle starts', async () => {
    renderMarketplace()
    fireEvent.click(await screen.findByRole('option', { name: /Laptop Support/ }))
    expect(await screen.findByText('Could not inspect this package')).toBeTruthy()
    expect(screen.getByRole('searchbox', { name: 'Search workflow packages' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Manage Sources' }))
    const sources = await screen.findByRole('dialog', { name: 'Workflow sources' })
    expect(within(sources).getByText('Upgrade Hermes to manage and refresh workflow sources.')).toBeTruthy()
    fireEvent.click(within(sources).getByRole('button', { name: 'Add source' }))
    fireEvent.change(within(sources).getByLabelText('Source name'), { target: { value: 'team' } })
    fireEvent.change(within(sources).getByLabelText('Repository URL'), {
      target: { value: 'https://example.test/team/workflows.git' }
    })
    fireEvent.click(within(sources).getByRole('button', { name: 'Save source' }))
    await waitFor(() =>
      expect(api.add).toHaveBeenCalledWith(
        { enabled: true, name: 'team', ref: null, repositoryUrl: 'https://example.test/team/workflows.git' },
        scopeA
      )
    )

    for (const name of ['Install package', 'Update package', 'Remove package', 'Review trust']) {
      expect(screen.queryByRole('button', { name })).toBeNull()
    }

    expect(api.inspect).not.toHaveBeenCalled()
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
      expect(screen.getByRole('button', { name: 'Remove package' }).hasAttribute('disabled')).toBe(false)
      expect(screen.getByRole('button', { name: 'Update package' }).hasAttribute('disabled')).toBe(true)
      expect(screen.getByRole('button', { name: 'Review trust' }).hasAttribute('disabled')).toBe(true)
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
