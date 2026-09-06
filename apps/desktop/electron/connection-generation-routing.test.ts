import { expect, it } from 'vitest'
const module = await import('./connection-generation-routing').catch(() => null)

// Break caught: primary SSH teardown invalidates a bare pool alias, or transient/config-test teardown retires a live descriptor.
it('retires SSH authority using explicit ownership, preserving an already-invalidated replacement construction', () => {
  expect(module!.invalidateSshGeneration).toBeTypeOf('function')
  const retired: string[] = []
  const invalidate = (scope: string) => retired.push(scope)
  module!.invalidateSshGeneration({ managedScope: 'primary' }, 'support', false, invalidate)
  module!.invalidateSshGeneration({ managedScope: 'pool', poolKey: 'conn:ssh::support' }, 'support', false, invalidate)
  module!.invalidateSshGeneration({ managedScope: 'transient' }, 'support', false, invalidate)
  module!.invalidateSshGeneration({ managedScope: 'primary' }, 'support', true, invalidate)
  expect(retired).toEqual(['primary', 'pool:conn:ssh::support'])
})

const original = {
  id: 'remote-a',
  kind: 'ssh',
  label: 'A',
  url: 'https://fixture.example',
  authMode: 'token',
  token: { encoding: 'plain', value: 'fixture' },
  host: 'host-a',
  user: 'fixture',
  port: 22,
  keyPath: '/fixture/key',
  remoteHermesPath: '/fixture/hermes',
  remoteProfile: 'support',
  headers: { 'X-Fixture': { encoding: 'plain', value: 'one' } }
}

// Break caught: a material field edit leaves the old route current before its async teardown.
it.each([
  ['kind', 'remote'],
  ['url', 'https://other.example'],
  ['authMode', 'oauth'],
  ['token', { encoding: 'plain', value: 'other' }],
  ['host', 'host-b'],
  ['user', 'other'],
  ['port', 2222],
  ['keyPath', '/other/key'],
  ['remoteHermesPath', '/other/hermes'],
  ['remoteProfile', 'other'],
  ['headers', { 'X-Fixture': { encoding: 'plain', value: 'two' } }]
])('invalidates exact registry scope before publishing changed %s', (field, value) => {
  expect(module).not.toBeNull()
  const invalidated: string[] = []
  module!.invalidateRegistryConfiguration(
    { primary: 'local', connections: [original] },
    { primary: 'local', connections: [{ ...original, [field]: value }] },
    { primary: () => invalidated.push('primary'), registry: id => invalidated.push(id) }
  )
  expect(invalidated).toEqual(['remote-a'])
})

// Break caught: foreground navigation/last-used metadata retires otherwise unchanged native descriptors.
it('retains native authority on last-used/launch/label edits but retires deletion and primary routing changes', () => {
  expect(module).not.toBeNull()
  const invalidated: string[] = []
  const ports = { primary: () => invalidated.push('primary'), registry: (id: string) => invalidated.push(id) }
  const before = { primary: 'local', connections: [original], lastUsed: 'remote-a' }
  module!.invalidateRegistryConfiguration(
    before,
    { ...before, lastUsed: 'remote-b', connections: [{ ...original, label: 'New label' }] },
    ports
  )
  expect(invalidated).toEqual([])
  module!.invalidateRegistryConfiguration(before, { primary: 'remote-a', connections: [] }, ports)
  expect(invalidated).toEqual(['primary', 'remote-a'])
})

// Break caught: legacy token/header/mode or per-profile reconfiguration misses its native cached backend.
it('invalidates global legacy routing and exact profile overrides without crossing registry pools', () => {
  expect(module).not.toBeNull()
  const invalidated: string[] = []

  const ports = {
    primary: () => invalidated.push('primary'),
    legacy: (profile: string) => invalidated.push(profile),
    primaryProfile: 'support',
    poolKeys: ['support', 'other', 'conn:remote-a::support']
  }

  const before = { mode: 'local', remote: {}, profiles: { support: { mode: 'local' } } }
  module!.invalidateLegacyConfiguration(
    before,
    { ...before, profiles: { support: { mode: 'remote' }, other: { mode: 'ssh' } } },
    ports
  )
  expect(invalidated).toEqual(['primary', 'other'])
  invalidated.length = 0
  module!.invalidateLegacyConfiguration(
    before,
    { ...before, mode: 'remote', remote: { url: 'https://fixture.example' } },
    ports
  )
  expect(invalidated).toEqual(['primary', 'support', 'other'])
})
