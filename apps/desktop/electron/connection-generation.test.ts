import { describe, expect, it } from 'vitest'

const module = await import('./connection-generation').catch(() => null)

describe('native descriptor generation authority', () => {
  // Break caught: changing a registry alias leaves its shared underlying native descriptor usable through another alias.
  it('invalidates all aliases of a selected route while unrelated scopes remain current', () => {
    const authority = module!.createConnectionGenerationRegistry()
    authority.begin('primary')
    authority.begin('pool:other')
    authority.associate('legacy', 1)
    authority.associate('registry', 1)
    authority.associate('other', 2)
    expect(authority.invalidateRoutes).toBeTypeOf('function')
    authority.invalidateRoutes(route => route === 'registry')
    expect(authority.current('legacy')).toBeUndefined()
    expect(authority.isCurrent(1)).toBe(false)
    expect(authority.isCurrent(2)).toBe(true)
  })
  // Break caught: reissuing a cached descriptor changes identity or aliases another route.
  it.each([
    'primary',
    'registry:local',
    'registry:token',
    'registry:oauth',
    'registry:cloud',
    'registry:url',
    'registry:ssh',
    'profile:support'
  ])('%s retains identity until its native descriptor is replaced', scope => {
    expect(module).not.toBeNull()
    const authority = module!.createConnectionGenerationRegistry()
    const claim = authority.begin(scope)
    const descriptor = authority.publish(claim, { baseUrl: 'https://fixture.example', token: 'fixture-token' })
    expect(descriptor.connectionGeneration).toBe(1)
    authority.assertCurrent(1)

    expect(authority.publish(claim, descriptor).connectionGeneration).toBe(1)

    const replacement = authority.begin(scope)
    expect(replacement.generation).toBe(2)
    expect(() => authority.publish(claim, descriptor)).toThrow('marketplace_connection_generation_changed')
    expect(authority.publish(replacement, descriptor).connectionGeneration).toBe(2)
  })

  // Break caught: invalidation notifies before retiring current generations, or late async A publishes over B.
  it('retires current aliases synchronously before notifying and refuses late publication', async () => {
    expect(module).not.toBeNull()
    const observed: boolean[] = []
    const authority = module!.createConnectionGenerationRegistry(() => observed.push(authority.isCurrent(1)))
    const old = authority.begin('primary')
    authority.associate('legacy::support', 1)
    authority.associate('registry::support', 1)
    let finish!: (value: object) => void

    const pending = new Promise<object>(resolve => {
      finish = resolve
    }).then(value => authority.publish(old, value))

    authority.invalidate('primary')
    expect(observed).toEqual([false])
    expect(authority.current('legacy::support')).toBeUndefined()
    expect(authority.current('registry::support')).toBeUndefined()
    const fresh = authority.begin('primary')
    finish({})
    await expect(pending).rejects.toThrow('marketplace_connection_generation_changed')
    expect(authority.publish(fresh, {}).connectionGeneration).toBe(2)
  })

  // Break caught: overflow reuses 1, leaves an old alias current, or renderer recreation clears exhaustion.
  it('issues MAX_SAFE_INTEGER once then permanently exhausts all routes without numeric reuse', () => {
    expect(module).not.toBeNull()
    const notifications: string[] = []

    const authority = module!.createConnectionGenerationRegistry(
      reason => notifications.push(reason),
      Number.MAX_SAFE_INTEGER - 1
    )

    const last = authority.begin('primary')
    expect(last.generation).toBe(Number.MAX_SAFE_INTEGER)
    authority.associate('legacy::support', last.generation)

    for (let i = 0; i < 3; i++) {
      expect(() => authority.begin('new-window')).toThrow('marketplace_connection_generation_exhausted')
      expect(() => authority.assertCurrent(1)).toThrow('marketplace_connection_generation_exhausted')
      expect(() => authority.publish(last, {})).toThrow('marketplace_connection_generation_exhausted')
    }

    expect(authority.current('legacy::support')).toBeUndefined()
    expect(notifications).toEqual(['marketplace_connection_generation_exhausted'])
    expect(module!.createConnectionGenerationRegistry().begin('fresh-main').generation).toBe(1)
  })
})
