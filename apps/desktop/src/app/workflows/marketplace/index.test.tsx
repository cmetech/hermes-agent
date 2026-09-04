// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'

import { WorkflowMarketplaceApiError } from '@/api/workflow-marketplace'
import { I18nProvider } from '@/i18n'
import type {
  WorkflowMarketplaceCatalogPackage,
  WorkflowMarketplaceInstalledPackage,
  WorkflowMarketplaceOperation,
  WorkflowMarketplacePackageDetail
} from '@/types/hermes'

import { InstalledPackages } from './installed-packages'
import { MarketplacePackageDetail } from './package-detail'
import { MarketplacePackageList } from './package-list'
import { marketplaceKeys } from './query-keys'

import { WorkflowMarketplaceView } from './index'

const api = vi.hoisted(() => ({
  capabilities: vi.fn(),
  getOperation: vi.fn(),
  inspect: vi.fn(),
  installed: vi.fn(),
  search: vi.fn(),
  sources: vi.fn()
}))

vi.mock('@/hermes', () => ({
  getWorkflowMarketplaceCapabilities: (...args: unknown[]) => api.capabilities(...args),
  getWorkflowMarketplaceOperation: (...args: unknown[]) => api.getOperation(...args),
  inspectWorkflowPackage: (...args: unknown[]) => api.inspect(...args),
  isWorkflowMarketplaceUnsupportedError: (error: unknown) =>
    typeof error === 'object' && error !== null && 'code' in error && error.code === 'marketplace_unsupported',
  listInstalledWorkflowPackages: (...args: unknown[]) => api.installed(...args),
  listWorkflowMarketplaceSources: (...args: unknown[]) => api.sources(...args),
  searchWorkflowPackages: (...args: unknown[]) => api.search(...args)
}))

const NOW = '2026-09-04T00:00:00Z'
const DIGEST = 'a'.repeat(64)
const COMMIT = 'b'.repeat(40)
const OPERATION_ID = `wmop_${'c'.repeat(12)}_${'d'.repeat(32)}`
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
    started_at: null,
    state: 'pending',
    updated_at: NOW
  }
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
        enabled: true,
        name: 'company',
        ref: 'main',
        repository_url: 'https://example.test/company/workflows.git'
      },
      {
        enabled: false,
        name: 'disabled-source',
        ref: null,
        repository_url: 'ssh://git@example.test/company/disabled.git'
      }
    ]
  })
  api.search.mockReset().mockResolvedValue(page([packageItem()]))
  api.installed.mockReset().mockResolvedValue({ packages: [installedPackage()], profile: 'support' })
  api.inspect.mockReset().mockResolvedValue(succeededDetail())
  api.getOperation.mockReset().mockResolvedValue(succeededDetail())
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('marketplace query keys', () => {
  it('keys every backend-owned projection by connection, profile, filter, page, and detail identity', () => {
    const scopeKey = 'remote-a::support'

    expect(marketplaceKeys.capabilities(scopeKey)).toEqual(['workflow-marketplace', scopeKey, 'capabilities'])
    expect(marketplaceKeys.sources(scopeKey)).toEqual(['workflow-marketplace', scopeKey, 'sources'])
    expect(marketplaceKeys.installed(scopeKey)).toEqual(['workflow-marketplace', scopeKey, 'installed'])
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
