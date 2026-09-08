import { expect, it } from 'vitest'

import { createConnectionGenerationRegistry } from './connection-generation'
import * as generation from './connection-generation'
import { createConnectionGenerationRouting } from './connection-generation-routing'
import { dispatchLifecycleRequest } from './lifecycle-api-transport'

const path = '/api/plugins/workflow/marketplace/lifecycle/v2/operations'

// Break caught: a backend wrapper recaptures authority after a request is retired and resurrects its route on resolution.
it.each([null, 'remote-a'])(
  'keeps backend resolution bound to the original request without alias resurrection (%s)',
  async connectionId => {
    const authority = createConnectionGenerationRegistry()
    const key = JSON.stringify([connectionId, 'support'])
    const descriptor = authority.publish(authority.begin('primary'), { baseUrl: 'https://fixture.example' })
    const captured = { routeKey: key, lease: authority.captureRoute(key) }
    let finish!: (value: typeof descriptor) => void

    const pendingDescriptor = new Promise<typeof descriptor>(resolve => {
      finish = resolve
    })

    expect(generation.ensureConnectionGenerationRoute).toBeTypeOf('function')
    authority.retireRoute(key)
    await expect(
      generation.ensureConnectionGenerationRoute(authority, key, () => Promise.resolve(descriptor), captured)
    ).rejects.toThrow('marketplace_connection_generation_changed')
    expect(authority.current(key)).toBeUndefined()

    const current = { routeKey: key, lease: authority.captureRoute(key) }
    const pending = generation.ensureConnectionGenerationRoute(authority, key, () => pendingDescriptor, current)
    authority.retireRoute(key)
    finish(descriptor)
    await expect(pending).rejects.toThrow('marketplace_connection_generation_changed')
    expect(authority.current(key)).toBeUndefined()
    expect(authority.isCurrent(1)).toBe(true)
  }
)

for (const structured of [false, true]) {
  for (const connectionId of [null, 'remote-a']) {
    // Compatibility control: first-use has no association yet; nested resolution must not publish another route or recapture.
    it(`associates first-use only after nested resolution (${connectionId}, structured=${structured})`, async () => {
      const authority = createConnectionGenerationRegistry()
      const key = JSON.stringify([connectionId, 'support'])
      const descriptor = authority.publish(authority.begin('primary'), { baseUrl: 'https://fixture.example' })
      let capturedAtResolve: generation.ConnectionRouteLease | undefined

      const result = await dispatchLifecycleRequest(
        {
          path,
          connectionId,
          profile: 'support',
          expectedConnectionGeneration: 1,
          expectedMarketplacePrincipalBinding: 'b'.repeat(64)
        },
        structured,
        {
          authority,
          routeKey: () => key,
          resolve: async (_value, captured) => {
            capturedAtResolve = captured
            expect(authority.current(key)).toBeUndefined()

            const resolved = await generation.ensureConnectionGenerationRoute(
              authority,
              key,
              () =>
                generation.ensureConnectionGenerationRoute(
                  authority,
                  '[null,"default"]',
                  async () => descriptor,
                  captured
                ),
              captured
            )

            expect(authority.current(key)).toBeUndefined()
            expect(authority.current('[null,"default"]')).toBeUndefined()
            authority.associate(captured.routeKey, resolved.connectionGeneration, captured.lease)

            return { descriptor: resolved, path, routeKey: captured.routeKey }
          },
          accessToken: async () => null,
          fetchToken: async () => ({ accepted: true }),
          fetchCookie: async () => {
            throw new Error('wrong transport')
          }
        }
      )

      expect(result).toEqual({ accepted: true })
      expect(authority.current(key)).toBe(1)
      expect(capturedAtResolve!.lease).toBe(authority.captureRoute(key))
    })

    // Break caught: the dispatcher adopts a replacement lease after resolver association and returns an obsolete body.
    it(`retains request authority across the resolver continuation (${connectionId}, structured=${structured})`, async () => {
      const authority = createConnectionGenerationRegistry()

      const owners = createConnectionGenerationRouting(authority, {
        primaryProfile: () => 'default',
        poolKeys: () => []
      })

      const key = owners.routeKey(connectionId, 'support')

      const descriptor = authority.publish(authority.begin('primary'), {
        baseUrl: 'https://fixture.example',
        authMode: 'oauth'
      })

      authority.associate(key, 1)
      authority.associate(owners.routeKey(null, 'other'), 1)
      const effects: string[] = []

      const deps = {
        authority,
        routeKey: () => key,
        resolve: async () => {
          authority.associate(key, 1)
          // Runs after resolver success, before the dispatcher's await continuation.
          queueMicrotask(() => {
            authority.retireRoute(key)
            authority.associate(key, 1)
          })

          return { descriptor, path, routeKey: key }
        },
        accessToken: async () => {
          effects.push('auth')

          return null
        },
        fetchToken: async () => {
          effects.push('token')

          return { private: 'obsolete body' }
        },
        fetchCookie: async () => {
          effects.push('cookie')

          return { private: 'obsolete body' }
        }
      }

      await expect(
        dispatchLifecycleRequest(
          {
            path,
            connectionId,
            profile: 'support',
            expectedConnectionGeneration: 1,
            expectedMarketplacePrincipalBinding: 'b'.repeat(64)
          },
          structured,
          deps
        )
      ).rejects.toThrow('marketplace_connection_generation_changed')
      expect(effects).toEqual([])
      expect(authority.current(owners.routeKey(null, 'other'))).toBe(1)
      expect(authority.isCurrent(1)).toBe(true)
    })

    // Break caught: a still-live shared generation lets a retired resolver's private error escape on rejection.
    it(`suppresses errors from a retired resolver (${connectionId}, structured=${structured})`, async () => {
      const authority = createConnectionGenerationRegistry()

      const owners = createConnectionGenerationRouting(authority, {
        primaryProfile: () => 'default',
        poolKeys: () => []
      })

      const key = owners.routeKey(connectionId, 'support')
      authority.begin('primary')
      authority.associate(key, 1)
      let reject!: (error: Error) => void

      const resolution = new Promise<never>((_resolve, fail) => {
        reject = fail
      })

      const deps = {
        authority,
        routeKey: () => key,
        resolve: () => resolution,
        accessToken: async () => null,
        fetchToken: async () => {
          throw new Error('must not fetch')
        },
        fetchCookie: async () => {
          throw new Error('must not fetch')
        }
      }

      const pending = dispatchLifecycleRequest(
        {
          path,
          connectionId,
          profile: 'support',
          expectedConnectionGeneration: 1,
          expectedMarketplacePrincipalBinding: 'b'.repeat(64)
        },
        structured,
        deps
      )

      authority.retireRoute(key)
      reject(new Error('private obsolete resolver error'))
      await expect(pending).rejects.toThrow('marketplace_connection_generation_changed')
      expect(authority.isCurrent(1)).toBe(true)
    })
  }

  // Compatibility control: an unretired first-use failure is preserved, but exhaustion always masks its old error.
  it(`keeps first-use error semantics and exhaustion precedence (structured=${structured})`, async () => {
    const authority = createConnectionGenerationRegistry(undefined, Number.MAX_SAFE_INTEGER - 1)
    const claim = authority.begin('primary')

    const request = {
      path,
      expectedConnectionGeneration: claim.generation,
      expectedMarketplacePrincipalBinding: 'b'.repeat(64)
    }

    const deps = {
      authority,
      routeKey: () => '[null,"default"]',
      resolve: async () => {
        throw new Error('current resolution failure')
      },
      accessToken: async () => null,
      fetchToken: async () => {
        throw new Error('must not fetch')
      },
      fetchCookie: async () => {
        throw new Error('must not fetch')
      }
    }

    await expect(dispatchLifecycleRequest(request, structured, deps)).rejects.toThrow('current resolution failure')
    await expect(
      dispatchLifecycleRequest(request, structured, {
        ...deps,
        resolve: async () => {
          expect(() => authority.begin('overflow')).toThrow('marketplace_connection_generation_exhausted')
          throw new Error('private old failure')
        }
      })
    ).rejects.toThrow('marketplace_connection_generation_exhausted')
  })
}
