import { afterEach, describe, expect, it } from 'vitest'

import { profileScopeKey } from '@/api/client'
import {
  createLifecycleHarness,
  deferredLifecycle,
  legacyInspectionFixture,
  lifecycleFixture,
  lifecycleIdentity,
  lifecycleStateFixture
} from '@/app/workflows/marketplace/lifecycle-test-harness'
import { marketplaceKeys } from '@/app/workflows/marketplace/query-keys'

const harnesses: ReturnType<typeof createLifecycleHarness>[] = []

const harness = () => {
  const h = createLifecycleHarness()
  harnesses.push(h)

  return h
}

afterEach(() => {
  harnesses.splice(0).forEach(h => h.dispose())
})

describe('package mutation reconciliation', () => {
  it.each([
    ['service recovery required', false, 'recovery_required'],
    ['service installed A trusted', true, 'busy'],
    ['service ambiguous state', false, 'unknown']
  ] as const)(
    'fences catalog after a later locked %s observation without a mutation record',
    async (fixture, busy, expected) => {
      const h = harness()
      const binding = await h.bind()
      const other = await h.bind({ connectionId: 'remote-b', profile: 'support' })
      const profile = await h.bind({ connectionId: 'remote-a', profile: 'personal' })
      const catalogKey = marketplaceKeys.catalog(profileScopeKey(binding))
      const otherKey = marketplaceKeys.catalog(profileScopeKey(other))
      const profileKey = marketplaceKeys.catalog(profileScopeKey(profile))
      h.state('service installed A trusted')
      await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
      await h.supervisor.reconcilePackage(other, lifecycleIdentity)
      await h.supervisor.reconcilePackage(profile, lifecycleIdentity)
      await h.queryClient.fetchQuery({ queryKey: catalogKey, queryFn: async () => ({ items: [] }) })
      await h.queryClient.fetchQuery({ queryKey: otherKey, queryFn: async () => ({ items: [] }) })
      await h.queryClient.fetchQuery({ queryKey: profileKey, queryFn: async () => ({ items: [] }) })
      expect(h.supervisor.canUseCatalog(binding, catalogKey)).toBe(true)
      h.state(fixture, false, busy)
      await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
      expect(h.supervisor.canUseCatalog(binding, catalogKey)).toBe(false)
      h.state('service installed A trusted')
      await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
      expect(h.supervisor.canUseCatalog(binding, catalogKey)).toBe(false)
      await h.queryClient.fetchQuery({ queryKey: catalogKey, queryFn: async () => ({ items: [] }) })
      const held = deferredLifecycle<unknown>()

      const oldRead = h.queryClient
        .fetchQuery({ queryKey: catalogKey, queryFn: () => held.promise })
        .catch(() => undefined)

      h.state(fixture, false, busy)
      await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
      expect(h.supervisor.getPackageGate(binding, lifecycleIdentity).state).toBe(expected)
      expect(h.supervisor.canUseCatalog(binding, catalogKey)).toBe(false)
      expect(h.supervisor.canUseCatalog(other, otherKey)).toBe(true)
      expect(h.supervisor.canUseCatalog(profile, profileKey)).toBe(true)
      expect(h.supervisor.$records.get()).toEqual([])
      h.state('service installed A trusted')
      await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
      held.resolve({ items: [] })
      await oldRead
      expect(h.supervisor.canUseCatalog(binding, catalogKey)).toBe(false)
      await h.queryClient.fetchQuery({ queryKey: catalogKey, queryFn: async () => ({ items: [] }) })
      expect(h.supervisor.canUseCatalog(binding, catalogKey)).toBe(true)
    }
  )
  it('requires a new exact post-barrier inspection admission rather than re-admitting an old ID or swapping poll IDs', async () => {
    const h = harness()
    const binding = await h.bind()
    const queryKey = marketplaceKeys.detail(profileScopeKey(binding), 'company', 'laptop-support')
    const old = legacyInspectionFixture('service inspect')
    await h.queryClient.fetchQuery({ queryKey, queryFn: async () => old })
    await h.mutate(binding)
    await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
    await h.queryClient.fetchQuery({ queryKey, queryFn: async () => old })
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity, queryKey).state).toBe('reconciling')
    const nextId = 'wmop_62e05a9a7e00_00000000000000000000000000000bba'
    await h.queryClient.fetchQuery({
      queryKey,
      queryFn: async () => legacyInspectionFixture('service inspect pending', nextId)
    })
    const pollKey = marketplaceKeys.operation(profileScopeKey(binding), nextId)
    await h.queryClient.fetchQuery({ queryKey: pollKey, queryFn: async () => old })
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity, pollKey).state).toBe('reconciling')
    await h.queryClient.fetchQuery({
      queryKey: pollKey,
      queryFn: async () => legacyInspectionFixture('service inspect', nextId)
    })
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity, pollKey).state).toBe('ready')
  })

  it.each(['principal', 'epoch', 'generation'] as const)(
    'cannot carry a known inspection admission across a changed %s',
    async kind => {
      const h = harness()
      const binding = await h.bind()
      const completed = legacyInspectionFixture('service inspect')
      const queryKey = marketplaceKeys.detail(profileScopeKey(binding), 'company', 'laptop-support')
      await h.queryClient.fetchQuery({ queryKey, queryFn: async () => completed })
      h.disconnect()
      h.changeAuthority(kind)
      const fresh = await h.bind()
      await h.supervisor.reconcilePackage(fresh, lifecycleIdentity)
      const pollKey = marketplaceKeys.operation(profileScopeKey(fresh), completed.id)
      await h.queryClient.fetchQuery({ queryKey: pollKey, queryFn: async () => completed })
      expect(h.supervisor.getPackageGate(fresh, lifecycleIdentity, pollKey).state).toBe('reconciling')
    }
  )

  it.each([
    { connectionId: 'remote-b', profile: 'support' },
    { connectionId: 'remote-a', profile: 'personal' }
  ])('does not borrow inspection admission from another scope $connectionId/$profile', async scope => {
    const h = harness()
    const binding = await h.bind()
    const completed = legacyInspectionFixture('service inspect')
    await h.queryClient.fetchQuery({
      queryKey: marketplaceKeys.detail(profileScopeKey(binding), 'company', 'laptop-support'),
      queryFn: async () => completed
    })
    const other = await h.bind(scope)
    await h.supervisor.reconcilePackage(other, lifecycleIdentity)
    const pollKey = marketplaceKeys.operation(profileScopeKey(other), completed.id)
    await h.queryClient.fetchQuery({ queryKey: pollKey, queryFn: async () => completed })
    expect(h.supervisor.getPackageGate(other, lifecycleIdentity, pollKey).state).toBe('reconciling')
  })

  it('does not borrow a detail admission made for a different package query identity', async () => {
    const h = harness()
    const binding = await h.bind()
    const completed = legacyInspectionFixture('service inspect')
    await h.queryClient.fetchQuery({
      queryKey: marketplaceKeys.detail(profileScopeKey(binding), 'company', 'different-package'),
      queryFn: async () => legacyInspectionFixture('service inspect pending')
    })
    await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
    const pollKey = marketplaceKeys.operation(profileScopeKey(binding), completed.id)
    await h.queryClient.fetchQuery({ queryKey: pollKey, queryFn: async () => completed })
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity, pollKey).state).toBe('reconciling')
  })

  it.each(['principal', 'epoch', 'generation'] as const)(
    'does not carry an unsafe package observation across a changed %s',
    async kind => {
      const h = harness()
      const binding = await h.bind()
      h.state('service recovery required')
      await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
      h.disconnect()
      h.changeAuthority(kind)
      const fresh = await h.bind()
      h.state('service installed A trusted')
      await h.supervisor.reconcilePackage(fresh, lifecycleIdentity)
      const catalogKey = marketplaceKeys.catalog(profileScopeKey(fresh))
      await h.queryClient.fetchQuery({ queryKey: catalogKey, queryFn: async () => ({ items: [] }) })
      expect(h.supervisor.canUseCatalog(fresh, catalogKey)).toBe(true)
      expect(h.supervisor.canUseCatalog(binding, catalogKey)).toBe(false)
    }
  )
  it('never renews the admission generation by polling the same immutable inspection after a mutation', async () => {
    const h = harness()
    const binding = await h.bind()
    const scope = profileScopeKey(binding)
    const admissionKey = marketplaceKeys.detail(scope, 'company', 'laptop-support')
    const pending = lifecycleFixture('service inspect pending')
    const completed = lifecycleFixture('service inspect')
    const operationKey = marketplaceKeys.operation(scope, pending.id)
    await h.queryClient.fetchQuery({ queryKey: admissionKey, queryFn: async () => pending })
    await h.queryClient.fetchQuery({ queryKey: operationKey, queryFn: async () => completed })
    await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity, operationKey).state).toBe('ready')
    await h.mutate(binding, 'service update confirm')
    h.state('service installed A trusted')
    await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
    await h.queryClient
      .fetchQuery({
        queryKey: admissionKey,
        queryFn: async () => {
          throw new Error('new inspection unavailable')
        }
      })
      .catch(() => undefined)
    await h.queryClient.fetchQuery({ queryKey: operationKey, queryFn: async () => completed })
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity, operationKey).state).toBe('reconciling')
    h.queryClient.removeQueries({ queryKey: operationKey, exact: true })
    await h.queryClient.fetchQuery({ queryKey: operationKey, queryFn: async () => completed })
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity, operationKey).state).toBe('reconciling')
  })

  it('does not treat historical inspection retrieval without a known admission as candidate freshness', async () => {
    const h = harness()
    const binding = await h.bind()
    await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
    const completed = lifecycleFixture('service inspect')
    const operationKey = marketplaceKeys.operation(profileScopeKey(binding), completed.id)
    await h.queryClient.fetchQuery({ queryKey: operationKey, queryFn: async () => completed })
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity, operationKey).state).toBe('reconciling')
  })
  it('keeps an explicit unknown terminal outcome unknown until a locked current-state read succeeds', async () => {
    const h = harness()
    const binding = await h.bind()
    h.invalidTerminalEvidence()
    await h.mutate(binding, 'service rollback failed')
    expect(h.supervisor.$records.get()[0].status).toBe('terminal')
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity).state).toBe('unknown')
    h.state('service installed A trusted')
    await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity).state).toBe('ready')
    expect(h.supervisor.$records.get()[0].operation?.outcome?.type).toBe('outcome_unknown')
  })
  it.each(['service install confirm', 'service update confirm', 'service remove confirm', 'service grant one A'])(
    '%s stays reconciling when all current-state reads fail',
    async name => {
      const h = harness()
      const binding = await h.bind()
      h.failOriginRefetches()
      await h.mutate(binding, name)
      await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
      expect(h.supervisor.getPackageGate(binding, lifecycleIdentity).state).toBe('reconciling')
      expect(h.supervisor.getPackageGate(binding, lifecycleIdentity).packageState).toBeNull()
      expect(h.supervisor.$records.get()[0].barrier).toBe(true)
    }
  )

  it('uses locked current state after verified rollback, never terminal history as current truth', async () => {
    const h = harness()
    const binding = await h.bind()
    await h.mutate(binding, 'service verified rollback')
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity).state).toBe('reconciling')
    h.state('service installed A trusted')
    await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity)).toMatchObject({
      state: 'ready',
      packageState: { state: 'installed' }
    })
    expect(h.supervisor.$records.get()[0].operation?.outcome?.type).toBe('known_unchanged')
  })

  it('refuses state read started before admission even when it completes after success', async () => {
    const h = harness()
    const binding = await h.bind()
    const held = h.holdState()
    const earlier = h.supervisor.reconcilePackage(binding, lifecycleIdentity)
    await h.mutate(binding)
    held.resolve(lifecycleStateFixture('service absent'))
    await earlier
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity).state).toBe('reconciling')
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity).packageState).toBeNull()
  })

  it('cancels old origin queries before POST, including every detail alias, while preserving another origin', async () => {
    const h = harness()
    const binding = await h.bind()
    const key = profileScopeKey(binding)
    const aborted: string[] = []

    const keys = [
      marketplaceKeys.installed(key),
      marketplaceKeys.searchRoot(key),
      marketplaceKeys.detail(key, 'company', 'laptop-support'),
      marketplaceKeys.detail(key, 'renamed-source', 'laptop-support'),
      ['workflow-catalog', key]
    ]

    for (const queryKey of keys) {
      void h.queryClient
        .fetchQuery({
          queryKey,
          queryFn: ({ signal }) => {
            signal.addEventListener('abort', () => aborted.push(JSON.stringify(queryKey)))

            return new Promise(() => undefined)
          }
        })
        .catch(() => undefined)
    }

    const other = marketplaceKeys.installed('remote-b::support')
    h.queryClient.setQueryData(other, { packages: ['another origin'] })
    h.onPost(() => {
      expect(aborted).toHaveLength(keys.length)
      expect(h.queryClient.getQueryData(other)).toEqual({ packages: ['another origin'] })
    })
    await h.mutate(binding)
    // A thrown POST assertion is caught as network failure by the supervisor, so assert its real record too.
    expect(h.supervisor.$records.get()[0].status).toBe('terminal')
    expect(aborted).toHaveLength(keys.length)
  })

  it('keeps rollback failure recovery-required until a new locked read proves recovery clear', async () => {
    const h = harness()
    const binding = await h.bind()
    await h.mutate(binding, 'service rollback failed')
    h.state('service recovery required')
    await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity).state).toBe('recovery_required')
    expect(h.supervisor.$records.get()[0].barrier).toBe(true)
    h.state('service installed A trusted')
    await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity).state).toBe('ready')
    expect(h.supervisor.$records.get()[0].operation?.outcome).toEqual({
      type: 'recovery_required',
      reason: 'rollback_failed'
    })
  })

  it('replaces the complete trust snapshot and accepts verified absence without retaining earlier members', async () => {
    const h = harness()
    const binding = await h.bind()
    h.state('service installed A trusted')
    await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
    expect(
      h.supervisor
        .getPackageGate(binding, lifecycleIdentity)
        .packageState?.trust?.workflows.find(item => item.workflow_name === 'A')?.state
    ).toBe('trusted')
    await h.mutate(binding, 'service update confirm')
    h.state('service installed untrusted')
    await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
    expect(
      h.supervisor
        .getPackageGate(binding, lifecycleIdentity)
        .packageState?.trust?.workflows.every(item => item.state === 'untrusted')
    ).toBe(true)
    await h.mutate(binding, 'service remove confirm')
    h.state('service absent')
    await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity)).toMatchObject({
      state: 'ready',
      packageState: { state: 'absent', trust: null, installed: null }
    })
  })

  it('requires the exact fresh candidate projection for Update but lets a locked local state suffice for Remove', async () => {
    const h = harness()
    const binding = await h.bind()
    const alias = marketplaceKeys.detail(profileScopeKey(binding), 'renamed-source', 'laptop-support')
    h.queryClient.setQueryData(alias, { previous: 'candidate' })
    await h.mutate(binding, 'service update confirm')
    h.state('service installed untrusted')
    await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity).state).toBe('ready')
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity, alias).state).toBe('reconciling')
    await h.queryClient.fetchQuery({ queryKey: alias, queryFn: async () => lifecycleFixture('service refresh') })
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity, alias).state).toBe('reconciling')
    await h.queryClient.fetchQuery({ queryKey: alias, queryFn: async () => lifecycleFixture('service inspect') })
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity, alias).state).toBe('ready')
  })

  it('does not promote stale detail completion or a successful installed-list refresh into current authority', async () => {
    const h = harness()
    const binding = await h.bind()
    const key = marketplaceKeys.detail(profileScopeKey(binding), 'company', 'laptop-support')
    const held = deferredLifecycle<unknown>()
    const earlier = h.queryClient.fetchQuery({ queryKey: key, queryFn: () => held.promise }).catch(() => undefined)
    await h.mutate(binding)
    h.state('service installed untrusted')
    await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
    held.resolve(lifecycleFixture('service inspect'))
    await earlier
    await h.queryClient.fetchQuery({
      queryKey: marketplaceKeys.installed(profileScopeKey(binding)),
      queryFn: async () => ({ packages: [] })
    })
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity, key).state).toBe('reconciling')
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity).packageState?.state).toBe('installed')
  })

  it('keeps a lost admission unknown even when a locked read reports absent', async () => {
    const h = harness()
    const binding = await h.bind()
    h.onPost(() => {
      throw new Error('response lost')
    })
    await h.mutate(binding)
    await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity)).toMatchObject({
      state: 'unknown',
      packageState: null
    })
    expect(h.supervisor.$records.get()[0]).toMatchObject({ status: 'admission_unknown', barrier: true })
  })

  it('reconciles current state after eviction without inventing historical success', async () => {
    const h = harness()
    const binding = await h.bind()
    h.onPost(() => {
      throw new Error('response lost')
    })
    await h.mutate(binding)
    h.evict()
    h.disconnect()
    const resumed = await h.bind()
    expect(h.supervisor.$records.get()[0].status).toBe('evicted')
    expect(h.supervisor.getPackageGate(resumed, lifecycleIdentity).state).toBe('unknown')
    h.state('service installed A trusted')
    await h.supervisor.reconcilePackage(resumed, lifecycleIdentity)
    expect(h.supervisor.getPackageGate(resumed, lifecycleIdentity).state).toBe('ready')
    expect(h.supervisor.$records.get()[0]).toMatchObject({ status: 'evicted', operation: null })
  })

  it('never selects current bytes from several terminal histories or source availability', async () => {
    const h = harness()
    const binding = await h.bind()
    const installed = await h.mutate(binding)
    h.state('service installed A trusted')
    await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
    await h.mutate(binding, 'service remove confirm')
    await h.queryClient.fetchQuery({
      queryKey: marketplaceKeys.sources(profileScopeKey(binding)),
      queryFn: async () => ({ sources: [] })
    })
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity).state).toBe('reconciling')
    h.state('service absent')
    await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
    await h.supervisor.retry(installed)
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity).packageState?.state).toBe('absent')
    expect(h.supervisor.$records.get()).toHaveLength(2)
  })

  it('advances again at terminal observation so a read begun during active work cannot release it', async () => {
    const h = harness()
    const binding = await h.bind()
    const key = await h.mutate(binding, 'service install confirm pending')
    const held = h.holdState()
    const during = h.supervisor.reconcilePackage(binding, lifecycleIdentity)
    h.complete('service install confirm')
    await h.supervisor.retry(key)
    held.resolve(lifecycleStateFixture('service absent'))
    await during
    expect(h.supervisor.getPackageGate(binding, lifecycleIdentity)).toMatchObject({
      state: 'reconciling',
      packageState: null
    })
  })

  it.each([
    { connectionId: 'remote-b', profile: 'support' },
    { connectionId: 'remote-a', profile: 'personal' }
  ])('keeps another origin %j usable while origin A reconciles and disconnects', async scope => {
    const h = harness()
    const a = await h.bind()
    const b = await h.bind(scope)
    h.state('service installed A trusted')
    await h.supervisor.reconcilePackage(b, lifecycleIdentity)
    await h.mutate(a)
    expect(h.supervisor.getPackageGate(b, lifecycleIdentity).state).toBe('ready')
    h.disconnect()
    expect(h.supervisor.getPackageGate(a, lifecycleIdentity).state).toBe('unknown')
    expect(h.supervisor.getPackageGate(b, lifecycleIdentity).state).toBe('ready')
    expect(h.supervisor.$records.get()[0].barrier).toBe(true)
  })

  it.each(['principal', 'epoch'] as const)(
    'preserves coordinator-owned %s purge against late catalog completion',
    async kind => {
      const h = harness()
      const binding = await h.bind()
      const queryKey = marketplaceKeys.catalog(profileScopeKey(binding))
      const held = deferredLifecycle<unknown>()
      const oldRead = h.queryClient.fetchQuery({ queryKey, queryFn: () => held.promise }).catch(() => undefined)
      const other = marketplaceKeys.catalog('remote-b::support')
      h.queryClient.setQueryData(other, { items: ['other authority'] })
      h.disconnect()
      h.changeAuthority(kind)
      const fresh = await h.bind()
      held.resolve({ items: ['old authority'] })
      await oldRead
      expect(h.queryClient.getQueryData(queryKey)).toBeUndefined()
      expect(h.queryClient.getQueryData(other)).toEqual({ items: ['other authority'] })
      expect(h.supervisor.canUseCatalog(fresh, queryKey)).toBe(false)
      expect(h.supervisor.getPackageGate(binding, lifecycleIdentity).state).toBe('unknown')
    }
  )

  it.each(['service recovery ambiguous', 'service rollback failed'])(
    '%s never turns a failed refetch into unchanged or ready',
    async name => {
      const h = harness()
      const binding = await h.bind()
      await h.mutate(binding, name)
      h.failOriginRefetches()
      await h.supervisor.reconcilePackage(binding, lifecycleIdentity)
      expect(h.supervisor.getPackageGate(binding, lifecycleIdentity)).toEqual({
        state: 'recovery_required',
        packageState: null
      })
    }
  )
})
