import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'

import { LifecycleApiError } from '@/api/workflow-marketplace-lifecycle'

import corpus from '../../../../tests/fixtures/workflow-marketplace-lifecycle-v2.json'

const module = await import('./workflow-marketplace-connection-binding').catch(() => null)
const scope = { connectionId: 'remote-a', profile: 'support' }
const root = ['workflow-marketplace', 'remote-a::support'] as const

function deferred<T>() {
  let resolve!: (value: T) => void

  const promise = new Promise<T>(done => {
    resolve = done
  })

  return { promise, resolve }
}

function harness() {
  expect(module).not.toBeNull()
  expect(module!.createMarketplaceBindingCoordinator).toBeTypeOf('function')
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  let generation = 1
  let capabilities: unknown = corpus.capabilities
  let probeCalls = 0
  let enteredProbe: (() => void) | undefined

  const coordinator = module!.createMarketplaceBindingCoordinator({
    queries,
    resolveConnection: async input => ({ connectionId: input.connectionId, connectionGeneration: generation }),
    getCapabilities: async input => {
      expect(input.connectionGeneration).toBe(generation)
      probeCalls++
      enteredProbe?.()
      enteredProbe = undefined

      return capabilities
    }
  })

  return {
    coordinator,
    queries,
    setGeneration: (value: number) => {
      generation = value
    },
    setCapabilities: (value: unknown) => {
      capabilities = value
    },
    probeCalls: () => probeCalls,
    nextProbe: () =>
      new Promise<void>(resolve => {
        enteredProbe = resolve
      })
  }
}

describe('marketplace binding privacy transitions', () => {
  it('rejects an unsupported response from a native generation that changed during the request', async () => {
    const queries = new QueryClient()
    let generation = 1

    const coordinator = module!.createMarketplaceBindingCoordinator({
      queries,
      resolveConnection: async input => ({ connectionId: input.connectionId, connectionGeneration: generation }),
      getCapabilities: async () => {
        generation = 2
        throw new LifecycleApiError('marketplace_lifecycle_unsupported', 0)
      }
    })

    try {
      await coordinator.probe(scope)
      expect(coordinator.legacyReadAttempt(scope)).toBeNull()
    } finally {
      queries.clear()
    }
  })

  it.each(['missing-generation', 'malformed', 'profile-mismatch'] as const)(
    'does not infer legacy authority from %s',
    async kind => {
      const { coordinator, queries, setGeneration, setCapabilities } = harness()

      try {
        if (kind === 'missing-generation') {
          setGeneration(undefined as never)
        } else {
          setCapabilities(kind === 'malformed' ? {} : { ...corpus.capabilities, profile: 'other' })
        }

        await coordinator.probe(scope)
        expect(coordinator.legacyReadAttempt(scope)).toBeNull()
      } finally {
        queries.clear()
      }
    }
  )
  // Break caught: settled package/source/trust values remain renderable while authority is unknown.
  it.each([
    'disconnect',
    'reconfigured',
    'marketplace_connection_generation_changed',
    'marketplace_principal_changed'
  ] as const)('quarantines immediately on %s and restores only after exact capability proof', async reason => {
    const { coordinator, queries, setGeneration, setCapabilities } = harness()
    const binding = await coordinator.probe(scope)
    queries.setQueryData([...root, 'installed'], ['old package'])
    queries.setQueryData([...root, 'sources'], ['old source'])
    queries.setQueryData([...root, 'trust'], ['old trust'])
    coordinator.suspend(scope, reason)
    expect(coordinator.presentation(scope, ['old package'])).toBeUndefined()
    setGeneration(2)
    const cap = deferred<unknown>()
    setCapabilities(cap.promise)
    const probing = coordinator.probe(scope)
    expect(coordinator.presentation(scope, ['old source'])).toBeUndefined()
    cap.resolve(corpus.capabilities)
    const rebound = await probing
    expect(rebound).toEqual({ ...binding, connectionGeneration: 2 })
    expect(coordinator.state(scope)).toMatchObject({ kind: 'last_observed' })
    expect(coordinator.presentation(scope, queries.getQueryData([...root, 'installed']))).toEqual(['old package'])
    expect(coordinator.isCurrent({ ...rebound! })).toBe(true)
    queries.clear()
  })

  // Break caught: actor/epoch change leaves any colliding marketplace row, or a canceled old query repopulates it.
  it.each(['principal_binding', 'registry_epoch'])(
    'purges the entire colliding query root on changed %s before accepting new data',
    async field => {
      const { coordinator, queries, setCapabilities, setGeneration } = harness()
      const old = await coordinator.probe(scope)

      for (const kind of ['installed', 'sources', 'search', 'trust', 'operation']) {
        queries.setQueryData([...root, kind], 'private old value')
      }

      queries.setQueryData(['workflow-marketplace', 'remote-b::support', 'installed'], 'other scope')
      const oldQuery = deferred<string>()

      const request = queries
        .fetchQuery({ queryKey: [...root, 'pending'], queryFn: () => oldQuery.promise })
        .catch(() => undefined)

      setGeneration(2)
      setCapabilities({
        ...corpus.capabilities,
        [field]: field === 'principal_binding' ? 'c'.repeat(64) : 'd'.repeat(32)
      })
      const next = await coordinator.probe(scope)
      expect(next).not.toBeNull()
      expect(queries.getQueriesData({ queryKey: root })).toEqual([])
      expect(queries.getQueryData(['workflow-marketplace', 'remote-b::support', 'installed'])).toBe('other scope')
      queries.setQueryData([...root, 'installed'], 'new actor')
      oldQuery.resolve('private late value')
      await request
      expect(queries.getQueryData([...root, 'pending'])).toBeUndefined()
      expect(coordinator.publish(old!, () => queries.removeQueries({ queryKey: root }))).toBe(false)
      expect(queries.getQueryData([...root, 'installed'])).toBe('new actor')
      queries.clear()
    }
  )

  // Break caught: capabilities resolving under A can bind/purge after B, or accepts a native generation race.
  it('discards old capabilities after native generation changes and after a newer probe settles', async () => {
    const { coordinator, queries, setCapabilities, setGeneration, nextProbe } = harness()
    await coordinator.probe(scope)
    const oldCap = deferred<unknown>()
    setCapabilities(oldCap.promise)
    const oldEntered = nextProbe()
    const oldProbe = coordinator.probe(scope)
    await oldEntered
    setGeneration(2)
    setCapabilities({ ...corpus.capabilities, principal_binding: 'c'.repeat(64) })
    const fresh = await coordinator.probe(scope)
    queries.setQueryData([...root, 'installed'], 'new actor')
    oldCap.resolve(corpus.capabilities)
    expect(await oldProbe).toBeNull()
    expect(coordinator.isCurrent(fresh!)).toBe(true)
    expect(queries.getQueryData([...root, 'installed'])).toBe('new actor')
    const race = deferred<unknown>()
    setCapabilities(race.promise)
    const entered = nextProbe()
    const pending = coordinator.probe(scope)
    await entered
    setGeneration(3)
    race.resolve(corpus.capabilities)
    expect(await pending).toBeNull()
    expect(coordinator.presentation(scope, 'old data')).toBeUndefined()
    queries.clear()
  })

  // Break caught: exhaustion retries capabilities, restores another scope, or exposes outcome claims.
  it('globally quarantines permanently on exhaustion and exposes restart guidance without changing unrelated work', async () => {
    const { coordinator, queries, probeCalls } = harness()
    await coordinator.probe(scope)
    await coordinator.probe({ connectionId: 'remote-b', profile: 'support' })
    coordinator.notify({ kind: 'exhausted' })
    expect(coordinator.state(scope)).toEqual({ kind: 'restart_required' })
    expect(coordinator.presentation(scope, 'secret')).toBeUndefined()
    expect(coordinator.presentation({ connectionId: 'remote-b', profile: 'support' }, 'other secret')).toBeUndefined()
    const before = probeCalls()
    expect(await coordinator.probe(scope)).toBeNull()
    expect(probeCalls()).toBe(before)
    queries.clear()
  })

  // Break caught: legacy descriptors or old capabilities are assigned synthetic lifecycle identity.
  it('disables missing native generation or binding without fallback', async () => {
    const { coordinator, queries, setGeneration, setCapabilities } = harness()
    setGeneration(undefined as never)
    expect(await coordinator.probe(scope)).toBeNull()
    expect(coordinator.state(scope)).toMatchObject({ kind: 'unsupported' })
    setGeneration(1)
    const { principal_binding: _binding, ...legacy } = corpus.capabilities
    setCapabilities(legacy)
    expect(await coordinator.probe(scope)).toBeNull()
    expect(coordinator.presentation(scope, 'old data')).toBeUndefined()
    queries.clear()
  })
})
