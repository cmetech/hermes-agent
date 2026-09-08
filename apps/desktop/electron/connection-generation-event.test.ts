import { EventEmitter } from 'node:events'

import { expect, it } from 'vitest'

const module = await import('./connection-generation-event').catch(() => null)

// Break caught: credential-bearing/ambiguous notifications are forwarded or unsubscribe leaves a listener active.
it('passes exact safe notifications through preload and unsubscribes completely', () => {
  expect(module).not.toBeNull()
  const ipc = new EventEmitter()
  const received: unknown[] = []
  const off = module!.subscribeConnectionGeneration(ipc, event => received.push(event))
  const scope = { kind: 'scope_invalidated', connectionId: null, profile: 'support' }
  ipc.emit('hermes:connection-generation', {}, scope)

  for (const invalid of [
    { ...scope, token: 'fixture-secret' },
    { kind: 'exhausted', connectionGeneration: 1 },
    { kind: 'scope_invalidated', profile: 'support' },
    { kind: 'other' }
  ]) {
    ipc.emit('hermes:connection-generation', {}, invalid)
  }

  ipc.emit('hermes:connection-generation', {}, { kind: 'exhausted' })
  expect(received).toEqual([scope, { kind: 'exhausted' }])
  off()
  ipc.emit('hermes:connection-generation', {}, scope)
  expect(received).toHaveLength(2)
  expect(ipc.listenerCount('hermes:connection-generation')).toBe(0)
})

// Break caught: only the foreground window is quarantined, or dead windows prevent delivery to healthy siblings.
it('broadcasts lifecycle notifications to every live renderer without gateway rehome', () => {
  expect(module).not.toBeNull()
  const calls: unknown[][] = []

  const windows = [false, true, false].map(destroyed => ({
    webContents: { isDestroyed: () => destroyed, send: (...args: unknown[]) => calls.push(args) }
  }))

  module!.broadcastConnectionGeneration(windows, { kind: 'exhausted' })
  expect(calls).toEqual([
    ['hermes:connection-generation', { kind: 'exhausted' }],
    ['hermes:connection-generation', { kind: 'exhausted' }]
  ])
})
