import { expect, it } from 'vitest'

import { createConnectionGenerationRegistry } from './connection-generation'
import { createConnectionGenerationRouting } from './connection-generation-routing'
import type { RegistryConnection } from './connection-registry'
import type { NativeTokenSet } from './native-oauth'
import * as cookie from './oauth-net-request'
import { resolveOauthPartition } from './oauth-partition'

const native = await import('./native-session-generation').catch(() => null)
const principalHeader = 'X-Hermes-Marketplace-Principal-Binding'
const principal = 'b'.repeat(64)

const registry: { primary: string; connections: RegistryConnection[] } = {
  primary: 'primary',
  connections: [
    { id: 'primary', kind: 'remote', label: 'Primary', authMode: 'oauth', url: 'https://primary.example' },
    { id: 'cloud', kind: 'cloud', label: 'Cloud', url: 'https://cloud.example' },
    { id: 'remote-a', kind: 'remote', label: 'A', authMode: 'oauth', url: 'https://host.example:8001' },
    { id: 'remote-b', kind: 'remote', label: 'B', authMode: 'oauth', url: 'https://host.example:8002' }
  ]
}

const config = { mode: 'remote', remote: { authMode: 'oauth', url: 'https://primary.example' } }
const partitionForUrl = (url: string) => resolveOauthPartition(url, { registry, v1RemoteUrl: config.remote.url })

// Break caught: the low-level cookie adapter accepts a native-owned principal for a normalized non-V2 destination.
it.each(['/api/status', '/api/plugins/workflow/marketplace/lifecycle/v2/.\t./operations'])(
  'refuses native lifecycle header delivery to %j at the cookie boundary',
  target => {
    let requests = 0
    expect(() =>
      cookie.createOauthJsonRequest(
        `https://host.example:8001${target}`,
        { nativeLifecycle: true, headers: { [principalHeader]: principal } },
        {
          sessionForUrl: () => ({}),
          request: () => {
            requests++

            return { setHeader: () => {} }
          },
          headersForUrl: () => ({})
        }
      )
    ).toThrow('Invalid workflow lifecycle request.')
    expect(requests).toBe(0)
  }
)

function liveRoutes() {
  const authority = createConnectionGenerationRegistry()
  const keys = ['conn:cloud::support', 'conn:remote-a::support', 'conn:remote-b::support']
  const owners = createConnectionGenerationRouting(authority, { primaryProfile: () => 'default', poolKeys: () => keys })

  for (const [index, scope] of ['primary', ...keys.map(key => `pool:${key}`)].entries()) {
    authority.begin(scope)
    authority.associate(owners.routeKey(registry.connections[index].id, 'support'), index + 1)
  }

  return { authority, owners }
}

// Break caught: replacing one cookie partition either misses cloud/shared-primary users or retires another isolated jar.
it('maps cookie-session replacement to the actual shared or isolated partition owners', () => {
  const { authority, owners } = liveRoutes()
  expect(owners.invalidateOauth).toBeTypeOf('function')
  owners.invalidateOauth('https://portal.example', registry, config, partitionForUrl, true)
  expect(authority.isCurrent(1)).toBe(false)
  expect(authority.isCurrent(2)).toBe(false)
  expect(authority.isCurrent(3)).toBe(true)
  expect(authority.isCurrent(4)).toBe(true)
  owners.invalidateOauth('https://host.example:8001', registry, config, partitionForUrl, true)
  expect(authority.isCurrent(3)).toBe(false)
  expect(authority.isCurrent(4)).toBe(true)
})

// Break caught: native login/logout publishes credentials before retiring current descriptors; refresh needlessly retires them.
it('retires native session authority before replacement/removal but preserves refresh generations', () => {
  expect(native).not.toBeNull()
  const { authority, owners } = liveRoutes()
  const url = 'https://host.example:8001'

  const old: NativeTokenSet = {
    accessToken: 'fixture-old-access',
    refreshToken: 'fixture-old-refresh',
    expiresAt: 1000,
    provider: 'fixture',
    userId: 'actor'
  }

  const replacement = { ...old, accessToken: 'fixture-new-access' }
  const tokens = new Map([[url, old]])
  const events: unknown[] = []

  const sessions = native!.createNativeSessionGeneration({
    tokens,
    invalidate: baseUrl => {
      events.push(tokens.get(baseUrl)?.accessToken)
      owners.invalidateOauth(baseUrl, registry, config, partitionForUrl)
    },
    persist: (baseUrl, value) => {
      events.push([authority.isCurrent(3), tokens.get(baseUrl)?.accessToken, value?.accessToken])
    }
  })

  sessions.store(url, replacement, false)
  expect(authority.isCurrent(3)).toBe(true)
  expect(events).toEqual([[true, 'fixture-new-access', 'fixture-new-access']])
  events.length = 0
  sessions.store(url, old)
  expect(events).toEqual(['fixture-new-access', [false, 'fixture-old-access', 'fixture-old-access']])
  expect(authority.isCurrent(4)).toBe(true)
  authority.begin('pool:conn:remote-a::support')
  sessions.clear(url)
  expect(tokens.has(url)).toBe(false)
  expect(authority.isCurrent(5)).toBe(false)
  expect(events.slice(-2)).toEqual(['fixture-old-access', [false, undefined, undefined]])
})

// Break caught: the actual cookie adapter chooses another jar, reinjects a config collision, or forwards the principal on a redirect.
it('constructs the Electron cookie request with scoped session, singleton native header, and no lifecycle redirects', () => {
  expect(cookie.createOauthJsonRequest).toBeTypeOf('function')
  const requests: Array<Record<string, unknown>> = []
  const headers: Array<[string, string]> = []
  const session = { partition: 'persist:hermes-remote-oauth:conn:remote-a' }
  const request = { setHeader: (name: string, value: string) => headers.push([name, value]) }

  const deps = {
    sessionForUrl: (url: string) => {
      expect(partitionForUrl(url)).toBe(session.partition)

      return session
    },
    request: (options: Record<string, unknown>) => {
      requests.push(options)

      return request
    },
    headersForUrl: () => ({ 'x-hermes-marketplace-principal-binding': 'config-collision', 'X-Proxy': 'fixture-proxy' })
  }

  cookie.createOauthJsonRequest(
    'https://host.example:8001/api/plugins/workflow/marketplace/lifecycle/v2/operations',
    {
      nativeLifecycle: true,
      headers: { [principalHeader]: principal, 'x-HeRmEs-MaRkEtPlAcE-PrInCiPaL-BiNdInG': 'descriptor-collision' }
    },
    deps
  )
  expect(requests[0]).toEqual({
    method: 'GET',
    url: 'https://host.example:8001/api/plugins/workflow/marketplace/lifecycle/v2/operations',
    session,
    useSessionCookies: true,
    redirect: 'error'
  })
  expect(headers.filter(([name]) => name.toLowerCase() === principalHeader.toLowerCase())).toEqual([
    [principalHeader, principal]
  ])
  headers.length = 0
  cookie.createOauthJsonRequest('https://host.example:8001/api/status', {}, deps)
  expect(requests[1].redirect).toBe('follow')
  expect(headers).toContainEqual(['X-Proxy', 'fixture-proxy'])
  expect(headers.some(([name]) => name.toLowerCase() === principalHeader.toLowerCase())).toBe(false)
})
