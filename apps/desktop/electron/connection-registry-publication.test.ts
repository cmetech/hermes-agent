import { mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { expect, it } from 'vitest'

import { createConnectionGenerationRegistry } from './connection-generation'
import { createConnectionGenerationRouting } from './connection-generation-routing'
import { dispatchLifecycleRequest } from './lifecycle-api-transport'

const publication = await import('./connection-registry-publication').catch(() => null)
const root = '/api/plugins/workflow/marketplace/lifecycle/v2'
const remote = { id: 'remote-a', kind: 'remote', label: 'A', authMode: 'token', url: 'https://old.example' }

// Break caught: failed migration/drift persistence publishes a changed/removed registry while old native authority survives.
it.each(['changed', 'removed'])(
  'invalidates live %s registry entries before failed-write fallback publication',
  async change => {
    expect(publication).not.toBeNull()
    const directory = mkdtempSync(path.join(os.tmpdir(), 'hermes-registry-publication-'))

    try {
      for (const structured of [false, true]) {
        let cache = { primary: 'local', connections: [remote] }
        const events: string[] = []
        const authority = createConnectionGenerationRegistry(() => events.push('invalidated'))

        const owners = createConnectionGenerationRouting(authority, {
          primaryProfile: () => 'default',
          poolKeys: () => ['conn:remote-a::support']
        })

        const descriptor = authority.publish(authority.begin('pool:conn:remote-a::support'), {
          baseUrl: remote.url,
          token: 'fixture'
        })

        authority.associate('["remote-a","support"]', 1)
        let finish!: (value: unknown) => void
        let entered!: () => void

        const fetched = new Promise<void>(resolve => {
          entered = resolve
        })

        const response = new Promise<unknown>(resolve => {
          finish = resolve
        })

        const pending = dispatchLifecycleRequest(
          {
            path: `${root}/operations`,
            expectedConnectionGeneration: 1,
            expectedMarketplacePrincipalBinding: 'b'.repeat(64)
          },
          structured,
          {
            authority,
            routeKey: () => '["remote-a","support"]',
            resolve: async () => ({ descriptor, routeKey: '["remote-a","support"]', path: `${root}/operations` }),
            accessToken: async () => null,
            fetchToken: async () => {
              entered()

              return response
            },
            fetchCookie: async () => {
              throw new Error('wrong transport')
            }
          }
        )

        await fetched

        const next = {
          primary: 'local',
          connections: change === 'removed' ? [] : [{ ...remote, url: 'https://new.example' }]
        }

        let currentAtPublication: number | undefined
        publication!.publishConnectionRegistry(
          next,
          {
            current: () => cache,
            persist: () => {
              writeFileSync(directory, 'cannot replace a directory')

              return 1
            },
            invalidate: owners.invalidateRegistryConfiguration,
            assign: (value, mtime) => {
              currentAtPublication = authority.current('["remote-a","support"]')
              cache = value
              events.push(`published:${mtime}`)
            }
          },
          { persist: true, fallback: true }
        )
        finish({ private: 'old registry body' })

        const outcome = await pending.then(
          value => ({ value }),
          error => ({ error: error.message })
        )

        expect(currentAtPublication).toBeUndefined()
        expect(events).toEqual(['invalidated', 'published:null'])
        expect(cache).toEqual(next)
        expect(outcome).toEqual({ error: 'marketplace_connection_generation_changed' })
      }
    } finally {
      rmSync(directory, { recursive: true, force: true })
    }
  }
)

// Break caught: the success path publishes/invalidate twice, or an ordinary failed save replaces the cache.
it('publishes a successful write once and leaves normal failed saves unpublished', () => {
  expect(publication).not.toBeNull()
  const directory = mkdtempSync(path.join(os.tmpdir(), 'hermes-registry-publication-'))

  try {
    const file = path.join(directory, 'connections.json')
    let cache = { primary: 'local', connections: [remote] }
    const next = { ...cache, connections: [] }
    const events: string[] = []

    const deps = {
      current: () => cache,
      persist: value => {
        writeFileSync(file, JSON.stringify(value))

        return statSync(file).mtimeMs
      },
      invalidate: () => events.push('invalidated'),
      assign: value => {
        cache = value
        events.push('published')
      }
    }

    publication!.publishConnectionRegistry(next, deps, { persist: true })
    expect(JSON.parse(readFileSync(file, 'utf8'))).toEqual(next)
    expect(events).toEqual(['invalidated', 'published'])
    expect(() =>
      publication!.publishConnectionRegistry(
        { ...next, primary: 'other' },
        {
          ...deps,
          persist: () => {
            writeFileSync(directory, 'invalid')

            return 0
          }
        },
        { persist: true }
      )
    ).toThrow()
    expect(cache).toEqual(next)
    expect(events).toEqual(['invalidated', 'published'])
  } finally {
    rmSync(directory, { recursive: true, force: true })
  }
})
