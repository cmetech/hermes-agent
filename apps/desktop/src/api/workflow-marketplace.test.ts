// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setApiRequestConnection, setApiRequestProfile } from '@/hermes'

import {
  addWorkflowMarketplaceSource,
  cancelWorkflowMarketplaceOperation,
  checkWorkflowPackageUpdates,
  confirmWorkflowPackageInstall,
  confirmWorkflowPackageRemoval,
  confirmWorkflowPackageUpdate,
  getWorkflowMarketplaceCapabilities,
  getWorkflowMarketplaceOperation,
  grantWorkflowPackageTrust,
  inspectWorkflowPackage,
  isWorkflowMarketplaceUnsupportedError,
  listInstalledWorkflowPackages,
  listWorkflowMarketplaceOperations,
  listWorkflowMarketplaceSources,
  prepareWorkflowPackageInstall,
  prepareWorkflowPackageRemoval,
  prepareWorkflowPackageUpdate,
  refreshWorkflowMarketplaceSource,
  removeWorkflowMarketplaceSource,
  reviewWorkflowPackageTrust,
  revokeWorkflowPackageTrust,
  searchWorkflowPackages,
  setWorkflowMarketplaceSourceEnabled,
  updateWorkflowMarketplaceSource
} from './workflow-marketplace'

const NOW = '2026-09-04T00:00:00Z'
const OPERATION_ID = `wmop_${'a'.repeat(12)}_${'b'.repeat(32)}`
const TOKEN = 'confirmation-token-value-1234567890'

function pendingOperation() {
  return {
    created_at: NOW,
    error: null,
    finished_at: null,
    id: OPERATION_ID,
    kind: 'refresh',
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

function source() {
  return {
    enabled: true,
    name: 'company',
    ref: 'main',
    repository_url: 'https://example.test/team/workflows.git'
  }
}

const scope = { connectionId: 'remote-a', profile: 'support' }

describe('workflow marketplace API', () => {
  let api: ReturnType<typeof vi.fn>
  let apiStructured: ReturnType<typeof vi.fn>

  beforeEach(() => {
    api = vi.fn()
    apiStructured = vi.fn().mockResolvedValue({ ok: true, value: pendingOperation() })
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: { api, apiStructured }
    })
  })

  afterEach(() => {
    setApiRequestConnection(null)
    setApiRequestProfile(null)
    vi.restoreAllMocks()
    Reflect.deleteProperty(window, 'hermesDesktop')
  })

  it('routes capabilities and every source endpoint with exact methods and bodies', async () => {
    apiStructured
      .mockResolvedValueOnce({
        ok: true,
        value: {
          capabilities: ['sources', 'search', 'installed', 'updates', 'transactions', 'trust', 'operations'],
          profile: 'support',
          schema_version: 1
        }
      })
      .mockResolvedValueOnce({ ok: true, value: { profile: 'support', sources: [source()] } })
      .mockResolvedValueOnce({ ok: true, value: { profile: 'support', source: source(), status: 'created' } })
      .mockResolvedValueOnce({ ok: true, value: { profile: 'support', source: source(), status: 'updated' } })
      .mockResolvedValueOnce({ ok: true, value: { profile: 'support', source: source(), status: 'disabled' } })
      .mockResolvedValueOnce({ ok: true, value: { profile: 'support', source: source(), status: 'removed' } })

    await getWorkflowMarketplaceCapabilities(scope)
    await listWorkflowMarketplaceSources(scope)
    await addWorkflowMarketplaceSource(
      { enabled: true, name: 'company', ref: 'main', repositoryUrl: 'https://example.test/team/workflows.git' },
      scope
    )
    await updateWorkflowMarketplaceSource(
      'company',
      { enabled: true, ref: null, repositoryUrl: 'https://example.test/team/workflows.git' },
      scope
    )
    await setWorkflowMarketplaceSourceEnabled('company', false, scope)
    await removeWorkflowMarketplaceSource('company', scope)
    await refreshWorkflowMarketplaceSource('company', scope)

    expect(apiStructured.mock.calls.map(([request]) => request)).toEqual([
      { connectionId: 'remote-a', path: '/api/plugins/workflow/marketplace/capabilities', profile: 'support' },
      { connectionId: 'remote-a', path: '/api/plugins/workflow/marketplace/sources', profile: 'support' },
      {
        body: {
          enabled: true,
          name: 'company',
          ref: 'main',
          repositoryUrl: 'https://example.test/team/workflows.git'
        },
        connectionId: 'remote-a',
        method: 'POST',
        path: '/api/plugins/workflow/marketplace/sources',
        profile: 'support'
      },
      {
        body: { enabled: true, ref: null, repositoryUrl: 'https://example.test/team/workflows.git' },
        connectionId: 'remote-a',
        method: 'PUT',
        path: '/api/plugins/workflow/marketplace/sources/company',
        profile: 'support'
      },
      {
        body: { enabled: false },
        connectionId: 'remote-a',
        method: 'POST',
        path: '/api/plugins/workflow/marketplace/sources/company/enabled',
        profile: 'support'
      },
      {
        connectionId: 'remote-a',
        method: 'DELETE',
        path: '/api/plugins/workflow/marketplace/sources/company',
        profile: 'support'
      },
      {
        connectionId: 'remote-a',
        method: 'POST',
        path: '/api/plugins/workflow/marketplace/sources/company/refresh',
        profile: 'support'
      }
    ])
    expect(api).not.toHaveBeenCalled()
  })

  it('uses deterministic encoded queries for search and operation pagination', async () => {
    apiStructured
      .mockResolvedValueOnce({
        ok: true,
        value: {
          items: [],
          limit: 20,
          next_offset: null,
          offset: 10,
          profile: 'support',
          query: 'laptop & desk',
          source: 'company'
        }
      })
      .mockResolvedValueOnce({
        ok: true,
        value: { limit: 25, offset: 5, operations: [], profile: 'support' }
      })

    await searchWorkflowPackages('laptop & desk', scope, { limit: 20, offset: 10, source: 'company' })
    await listWorkflowMarketplaceOperations(scope, { limit: 25, offset: 5 })

    expect(apiStructured.mock.calls.map(([request]) => request.path)).toEqual([
      '/api/plugins/workflow/marketplace/packages?q=laptop+%26+desk&source=company&offset=10&limit=20',
      '/api/plugins/workflow/marketplace/operations?offset=5&limit=25'
    ])
  })

  it('routes package reads, update checks, and all prepare/confirm/trust actions', async () => {
    apiStructured
      .mockResolvedValueOnce({ ok: true, value: pendingOperation() })
      .mockResolvedValueOnce({ ok: true, value: { packages: [], profile: 'support' } })
      .mockResolvedValue({ ok: true, value: pendingOperation() })
    const packageIdentity = { packageId: 'laptop-support', sourceKey: 'company' }

    await inspectWorkflowPackage('company', 'laptop-support', scope)
    await listInstalledWorkflowPackages(scope)
    await checkWorkflowPackageUpdates(packageIdentity, scope)
    await checkWorkflowPackageUpdates(null, scope)
    await prepareWorkflowPackageInstall(
      { identifier: 'company/laptop-support', packagePath: 'packages/laptop-support', ref: 'main' },
      scope
    )
    await confirmWorkflowPackageInstall(TOKEN, scope)
    await prepareWorkflowPackageUpdate(packageIdentity, scope)
    await confirmWorkflowPackageUpdate(TOKEN, scope)
    await prepareWorkflowPackageRemoval(packageIdentity, scope)
    await confirmWorkflowPackageRemoval(TOKEN, scope)
    await reviewWorkflowPackageTrust(packageIdentity, 'laptop-diagnostic', scope)
    await grantWorkflowPackageTrust(TOKEN, scope)
    await revokeWorkflowPackageTrust(packageIdentity, undefined, scope)
    await getWorkflowMarketplaceOperation(OPERATION_ID, scope)
    await cancelWorkflowMarketplaceOperation(OPERATION_ID, scope)

    expect(apiStructured.mock.calls.map(([request]) => request)).toEqual([
      {
        connectionId: 'remote-a',
        path: '/api/plugins/workflow/marketplace/packages/company/laptop-support',
        profile: 'support'
      },
      { connectionId: 'remote-a', path: '/api/plugins/workflow/marketplace/installed', profile: 'support' },
      {
        body: { identity: packageIdentity },
        connectionId: 'remote-a',
        method: 'POST',
        path: '/api/plugins/workflow/marketplace/updates/check',
        profile: 'support'
      },
      {
        body: {},
        connectionId: 'remote-a',
        method: 'POST',
        path: '/api/plugins/workflow/marketplace/updates/check',
        profile: 'support'
      },
      {
        body: { identifier: 'company/laptop-support', packagePath: 'packages/laptop-support', ref: 'main' },
        connectionId: 'remote-a',
        method: 'POST',
        path: '/api/plugins/workflow/marketplace/install/prepare',
        profile: 'support'
      },
      {
        body: { confirmationToken: TOKEN },
        connectionId: 'remote-a',
        method: 'POST',
        path: '/api/plugins/workflow/marketplace/install/confirm',
        profile: 'support'
      },
      {
        body: packageIdentity,
        connectionId: 'remote-a',
        method: 'POST',
        path: '/api/plugins/workflow/marketplace/update/prepare',
        profile: 'support'
      },
      {
        body: { confirmationToken: TOKEN },
        connectionId: 'remote-a',
        method: 'POST',
        path: '/api/plugins/workflow/marketplace/update/confirm',
        profile: 'support'
      },
      {
        body: packageIdentity,
        connectionId: 'remote-a',
        method: 'POST',
        path: '/api/plugins/workflow/marketplace/remove/prepare',
        profile: 'support'
      },
      {
        body: { confirmationToken: TOKEN },
        connectionId: 'remote-a',
        method: 'POST',
        path: '/api/plugins/workflow/marketplace/remove/confirm',
        profile: 'support'
      },
      {
        body: { identity: packageIdentity, workflowName: 'laptop-diagnostic' },
        connectionId: 'remote-a',
        method: 'POST',
        path: '/api/plugins/workflow/marketplace/trust/review',
        profile: 'support'
      },
      {
        body: { confirmationToken: TOKEN },
        connectionId: 'remote-a',
        method: 'POST',
        path: '/api/plugins/workflow/marketplace/trust/grant',
        profile: 'support'
      },
      {
        body: { identity: packageIdentity },
        connectionId: 'remote-a',
        method: 'POST',
        path: '/api/plugins/workflow/marketplace/trust/revoke',
        profile: 'support'
      },
      {
        connectionId: 'remote-a',
        path: `/api/plugins/workflow/marketplace/operations/${OPERATION_ID}`,
        profile: 'support'
      },
      {
        connectionId: 'remote-a',
        method: 'POST',
        path: `/api/plugins/workflow/marketplace/operations/${OPERATION_ID}/cancel`,
        profile: 'support'
      }
    ])
  })

  it('omits undefined mutation fields and never puts confirmation tokens in URLs', async () => {
    apiStructured.mockResolvedValueOnce({
      ok: true,
      value: { profile: 'support', source: source(), status: 'created' }
    })
    await addWorkflowMarketplaceSource(
      { name: 'company', repositoryUrl: 'https://example.test/team/workflows.git' },
      scope
    )
    await prepareWorkflowPackageInstall({ identifier: 'company/laptop-support' }, scope)
    await reviewWorkflowPackageTrust({ packageId: 'laptop-support', sourceKey: 'company' }, undefined, scope)
    await confirmWorkflowPackageInstall(TOKEN, scope)

    expect(apiStructured.mock.calls.map(([request]) => request.body)).toEqual([
      { name: 'company', repositoryUrl: 'https://example.test/team/workflows.git' },
      { identifier: 'company/laptop-support' },
      { identity: { packageId: 'laptop-support', sourceKey: 'company' } },
      { confirmationToken: TOKEN }
    ])
    expect(apiStructured.mock.calls.map(([request]) => request.path).join(' ')).not.toContain(TOKEN)
  })

  it('accepts the backend-supported owner/repository/subdirectory install shorthand', async () => {
    await prepareWorkflowPackageInstall({ identifier: 'owner/repository/packages/laptop-support' }, scope)

    expect(apiStructured).toHaveBeenCalledWith({
      body: { identifier: 'owner/repository/packages/laptop-support' },
      connectionId: 'remote-a',
      method: 'POST',
      path: '/api/plugins/workflow/marketplace/install/prepare',
      profile: 'support'
    })
  })

  it('uses ambient scope but lets an explicit local/null scope override it', async () => {
    apiStructured.mockResolvedValue({ ok: true, value: { packages: [], profile: 'ambient-profile' } })
    setApiRequestConnection('remote-ambient')
    setApiRequestProfile('ambient-profile')

    await listInstalledWorkflowPackages()
    apiStructured.mockResolvedValue({ ok: true, value: { packages: [], profile: 'local-profile' } })
    await listInstalledWorkflowPackages({ connectionId: 'local', profile: 'local-profile' })
    apiStructured.mockResolvedValue({ ok: true, value: { packages: [], profile: 'default' } })
    await listInstalledWorkflowPackages({ connectionId: null, profile: null })

    expect(apiStructured.mock.calls.map(([request]) => request)).toEqual([
      {
        connectionId: 'remote-ambient',
        path: '/api/plugins/workflow/marketplace/installed',
        profile: 'ambient-profile'
      },
      { connectionId: 'local', path: '/api/plugins/workflow/marketplace/installed', profile: 'local-profile' },
      { connectionId: null, path: '/api/plugins/workflow/marketplace/installed', profile: null }
    ])
  })

  it('throws one stable TypeError for malformed backend data without embedding it', async () => {
    apiStructured.mockResolvedValue({ ok: true, value: { access_token: 'secret', packages: [] } })

    await expect(listInstalledWorkflowPackages(scope)).rejects.toEqual(
      new TypeError('Hermes returned invalid workflow marketplace data.')
    )
    await expect(listInstalledWorkflowPackages(scope)).rejects.not.toThrow(/secret/)
  })

  it('distinguishes an unsupported capability route from an ordinary package 404', async () => {
    apiStructured.mockResolvedValueOnce({ body: { detail: 'Not Found' }, ok: false, status: 404 })

    let unsupported: unknown

    try {
      await getWorkflowMarketplaceCapabilities(scope)
    } catch (error) {
      unsupported = error
    }

    expect(isWorkflowMarketplaceUnsupportedError(unsupported)).toBe(true)
    expect(unsupported).toMatchObject({ code: 'marketplace_unsupported', status: 404 })

    apiStructured.mockResolvedValueOnce({
      body: {
        detail: { code: 'package_not_found', message: 'Workflow marketplace request failed.' }
      },
      ok: false,
      status: 404
    })
    let ordinary: unknown

    try {
      await inspectWorkflowPackage('company', 'missing', scope)
    } catch (error) {
      ordinary = error
    }

    expect(ordinary).toMatchObject({ code: 'package_not_found', status: 404 })
    expect(isWorkflowMarketplaceUnsupportedError(ordinary)).toBe(false)
  })

  it('preserves stable error fields without retaining unknown response material', async () => {
    apiStructured.mockResolvedValue({
      body: {
        detail: {
          code: 'source_authentication_failed',
          debug: 'access_token=secret',
          message: 'Workflow marketplace request failed.'
        }
      },
      ok: false,
      status: 401
    })

    let caught: unknown

    try {
      await listWorkflowMarketplaceSources(scope)
    } catch (error) {
      caught = error
    }

    expect(caught).toMatchObject({ code: '401', message: 'HTTP 401', status: 401 })
    expect(JSON.stringify(caught)).not.toContain('secret')
    expect(caught).not.toHaveProperty('body')
  })

  it.each([
    () => searchWorkflowPackages('x'.repeat(257), scope),
    () => searchWorkflowPackages('ok', scope, { limit: 0 }),
    () => listWorkflowMarketplaceOperations(scope, { offset: Number.MAX_SAFE_INTEGER + 1 }),
    () => getWorkflowMarketplaceOperation('../escape', scope),
    () => confirmWorkflowPackageInstall('short', scope)
  ])('rejects unsafe client input before issuing a request', async call => {
    await expect(call()).rejects.toBeInstanceOf(TypeError)
    expect(apiStructured).not.toHaveBeenCalled()
  })
})
