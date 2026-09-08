import { expect, it } from 'vitest'

import { resolveProfileBackendRoute } from './connection-config'
import { createConnectionGenerationRegistry } from './connection-generation'
import * as routing from './connection-generation-routing'
import { dispatchLifecycleRequest } from './lifecycle-api-transport'
import { createPrimaryRemoteConnection } from './primary-backend-startup'

const ROOT = '/api/plugins/workflow/marketplace/lifecycle/v2'
const binding = 'b'.repeat(64)

function deferred<T>() {
  let resolve!: (value: T) => void

  const promise = new Promise<T>(done => {
    resolve = done
  })

  return { promise, resolve }
}

// Break caught: retiring then restoring a shared alias to the same live descriptor lets an in-flight old body pass numeric-only checks.
it('fences in-flight responses even if the retired alias returns to the same shared descriptor', async () => {
  const authority = createConnectionGenerationRegistry()
  const descriptor = authority.publish(authority.begin('primary'), { baseUrl: 'https://primary.example' })
  authority.associate('[null,"support"]', 1)
  const entered = deferred<void>()
  const response = deferred<unknown>()

  const old = dispatchLifecycleRequest(
    { path: `${ROOT}/operations`, expectedConnectionGeneration: 1, expectedMarketplacePrincipalBinding: binding },
    true,
    {
      authority,
      routeKey: () => '[null,"support"]',
      resolve: async () => ({ descriptor, path: `${ROOT}/operations`, routeKey: '[null,"support"]' }),
      accessToken: async () => null,
      fetchToken: async () => {
        entered.resolve()

        return response.promise
      },
      fetchCookie: async () => {
        throw new Error('wrong transport')
      }
    }
  )

  await entered.promise
  authority.retireRoute('[null,"support"]')
  authority.associate('[null,"support"]', 1)
  response.resolve({ private: 'pre-retirement body' })
  await expect(old).rejects.toThrow('marketplace_connection_generation_changed')
  expect(authority.isCurrent(1)).toBe(true)
})

// Break caught: resolving a formerly shared route after retirement reattaches the unchanged primary generation.
it('refuses late route association while permitting a fresh dedicated resolution', () => {
  const authority = createConnectionGenerationRegistry()
  expect(authority.captureRoute).toBeTypeOf('function')

  const owners = routing.createConnectionGenerationRouting(authority, {
    primaryProfile: () => 'default',
    poolKeys: () => []
  })

  authority.begin('primary')
  const oldResolution = authority.captureRoute('[null,"support"]')
  owners.invalidateLegacyConfiguration({ profiles: {} }, { profiles: { support: { mode: 'remote' } } })
  expect(() => authority.associate('[null,"support"]', 1, oldResolution)).toThrow(
    'marketplace_connection_generation_changed'
  )
  const freshResolution = authority.captureRoute('[null,"support"]')
  authority.begin('pool:support')
  authority.associate('[null,"support"]', 2, freshResolution)
  expect(authority.current('[null,"support"]')).toBe(2)
  expect(authority.isCurrent(1)).toBe(true)
})

// Break caught: a secondary leaving shared primary keeps its old alias current and returns the old actor's body.
it.each([false, true])(
  'retires only the reconfigured shared-profile alias before publication (structured=%s)',
  async structured => {
    expect(routing.createConnectionGenerationRouting).toBeTypeOf('function')
    const notifications: string[][] = []

    const authority = createConnectionGenerationRegistry((_reason, routes) => {
      notifications.push([...routes])
    })

    const owners = routing.createConnectionGenerationRouting(authority, {
      primaryProfile: () => 'default',
      poolKeys: () => []
    })

    const before = { mode: 'remote', remote: { url: 'https://primary.example' }, profiles: {} }
    const next = { ...before, profiles: { support: { mode: 'remote', url: 'https://dedicated.example' } } }
    expect(resolveProfileBackendRoute('support', { primaryProfile: 'default', globalRemote: true })).toEqual({
      backend: 'primary',
      descriptorProfile: 'support',
      scopePath: true
    })

    const descriptor = authority.publish(
      authority.begin('primary'),
      createPrimaryRemoteConnection(
        { baseUrl: 'https://primary.example', token: 'fixture-token', wsUrl: 'wss://primary.example/ws' },
        [],
        {}
      )
    )

    for (const profile of ['default', 'support', 'other']) {
      authority.associate(owners.routeKey(null, profile), descriptor.connectionGeneration)
    }

    const entered = deferred<void>()
    const response = deferred<unknown>()

    const old = dispatchLifecycleRequest(
      {
        path: `${ROOT}/operations`,
        profile: 'support',
        expectedConnectionGeneration: 1,
        expectedMarketplacePrincipalBinding: binding
      },
      structured,
      {
        authority,
        routeKey: () => '[null,"support"]',
        resolve: async () => ({
          descriptor: { ...descriptor, token: descriptor.token as string },
          path: `${ROOT}/operations?profile=support`,
          routeKey: owners.routeKey(null, 'support')
        }),
        accessToken: async () => null,
        fetchToken: async () => {
          entered.resolve()

          return response.promise
        },
        fetchCookie: async () => {
          throw new Error('wrong transport')
        }
      }
    )

    await entered.promise
    owners.invalidateLegacyConfiguration(before, next)
    const retiredSynchronously = authority.current('[null,"support"]')
    const scopedNotifications = [...notifications]
    expect(
      resolveProfileBackendRoute('support', {
        primaryProfile: 'default',
        globalRemote: true,
        profileRemoteOverride: true
      })
    ).toEqual({ backend: 'pool', descriptorProfile: null, scopePath: false })
    const dedicated = authority.publish(authority.begin('pool:support'), { baseUrl: 'https://dedicated.example' })
    authority.associate(owners.routeKey(null, 'support'), dedicated.connectionGeneration)
    response.resolve({ private: 'old actor body' })

    const outcome = await old.then(
      value => ({ value }),
      error => ({ error: error.message })
    )

    expect(retiredSynchronously).toBeUndefined()
    expect(scopedNotifications).toEqual([['[null,"support"]']])
    expect(outcome).toEqual({ error: 'marketplace_connection_generation_changed' })
    expect(authority.current('[null,"support"]')).toBe(2)
    expect(authority.current('[null,"default"]')).toBe(1)
    expect(authority.current('[null,"other"]')).toBe(1)
    expect(authority.isCurrent(1)).toBe(true)
  }
)
