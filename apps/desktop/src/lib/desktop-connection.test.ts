import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { DesktopConnectionLifecycleSnapshot, HermesConnection } from '@/global'

import {
  _resetDesktopConnectionForTests,
  desktopConnectionScopeKey,
  ensureDesktopConnection,
  inspectDesktopConnection,
  normalizeDesktopConnectionScope
} from './desktop-connection'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void

  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve
    reject = onReject
  })

  return { promise, reject, resolve }
}

function connection(label: string): HermesConnection {
  return {
    baseUrl: `http://${label}`,
    connectionGeneration: 1,
    isFullscreen: false,
    logs: [],
    nativeOverlayWidth: 0,
    token: label,
    windowButtonPosition: null,
    wsUrl: `ws://${label}`
  }
}

function installBridge(overrides: Partial<Window['hermesDesktop']> = {}) {
  let lifecycle: ((snapshot: DesktopConnectionLifecycleSnapshot) => void) | null = null

  const bridge = {
    ensureConnection: vi.fn(),
    getConnection: vi.fn(),
    getProfileRoutes: vi.fn(),
    getGatewayWsUrl: vi.fn(),
    inspectConnection: vi.fn().mockResolvedValue({
      attemptId: null,
      elapsedMs: 0,
      phase: null,
      scope: { connectionId: null, profile: 'default' },
      state: 'absent'
    }),
    onConnectionLifecycle: vi.fn(callback => {
      lifecycle = callback

      return () => {
        lifecycle = null
      }
    }),
    revalidateConnection: vi.fn(),
    touchBackend: vi.fn(),
    ...overrides
  } as unknown as Window['hermesDesktop']

  window.hermesDesktop = bridge

  return {
    bridge,
    publish(snapshot: DesktopConnectionLifecycleSnapshot) {
      lifecycle?.(snapshot)
    }
  }
}

describe('desktop connection client', () => {
  beforeEach(() => {
    _resetDesktopConnectionForTests()
  })

  afterEach(() => {
    _resetDesktopConnectionForTests()
  })

  it('normalizes stable connection/profile scope keys', () => {
    expect(normalizeDesktopConnectionScope({ connectionId: ' office ', profile: ' work ' })).toEqual({
      connectionId: 'office',
      profile: 'work'
    })
    expect(desktopConnectionScopeKey({ connectionId: null, profile: null })).toBe('[null,"default"]')
  })

  it('shares one exact promise for concurrent callers in the same scope', async () => {
    const pending = deferred<HermesConnection>()
    const { bridge } = installBridge({ ensureConnection: vi.fn(() => pending.promise) })

    const first = ensureDesktopConnection({ connectionId: ' office ', profile: ' work ' })
    const second = ensureDesktopConnection({ connectionId: 'office', profile: 'work' })

    expect(second).toBe(first)
    expect(bridge.ensureConnection).toHaveBeenCalledTimes(1)
    pending.resolve(connection('office'))
    await first
  })

  it('reuses a ready descriptor without another bridge call', async () => {
    const ready = connection('ready')
    const { bridge } = installBridge({ ensureConnection: vi.fn().mockResolvedValue(ready) })

    await expect(ensureDesktopConnection({ profile: 'default' })).resolves.toBe(ready)
    await expect(ensureDesktopConnection({ profile: 'default' })).resolves.toBe(ready)
    expect(bridge.ensureConnection).toHaveBeenCalledTimes(1)
  })

  it('drops a ready descriptor when Electron publishes invalidation', async () => {
    const first = connection('first')
    const second = connection('second')

    const { bridge, publish } = installBridge({
      ensureConnection: vi.fn().mockResolvedValueOnce(first).mockResolvedValueOnce(second)
    })

    await ensureDesktopConnection({ profile: 'default' })
    publish({
      attemptId: null,
      elapsedMs: 0,
      phase: null,
      scope: { connectionId: null, profile: 'default' },
      state: 'absent'
    })

    await expect(ensureDesktopConnection({ profile: 'default' })).resolves.toBe(second)
    expect(bridge.ensureConnection).toHaveBeenCalledTimes(2)
  })

  it('keeps different connection and profile scopes isolated', async () => {
    const { bridge } = installBridge({
      ensureConnection: vi.fn(scope => Promise.resolve(connection(`${scope.connectionId ?? 'local'}-${scope.profile}`)))
    })

    const [local, office, research] = await Promise.all([
      ensureDesktopConnection({ profile: 'default' }),
      ensureDesktopConnection({ connectionId: 'office', profile: 'default' }),
      ensureDesktopConnection({ connectionId: 'office', profile: 'research' })
    ])

    expect(new Set([local.baseUrl, office.baseUrl, research.baseUrl]).size).toBe(3)
    expect(bridge.ensureConnection).toHaveBeenCalledTimes(3)
  })

  it('does not cache a result from an older Electron attempt', async () => {
    const oldAttempt = deferred<HermesConnection>()
    const newAttempt = deferred<HermesConnection>()

    const { bridge, publish } = installBridge({
      ensureConnection: vi.fn().mockReturnValueOnce(oldAttempt.promise).mockReturnValueOnce(newAttempt.promise)
    })

    const scope = { connectionId: null, profile: 'default' }
    const first = ensureDesktopConnection(scope)

    publish({ attemptId: 1, elapsedMs: 0, phase: 'launch', scope, state: 'starting' })
    publish({ attemptId: 2, elapsedMs: 0, phase: 'launch', scope, state: 'starting' })
    const second = ensureDesktopConnection(scope)

    oldAttempt.resolve(connection('stale'))
    newAttempt.resolve(connection('current'))
    await expect(first).resolves.toMatchObject({ baseUrl: 'http://stale' })
    await expect(second).resolves.toMatchObject({ baseUrl: 'http://current' })
    await expect(ensureDesktopConnection(scope)).resolves.toMatchObject({ baseUrl: 'http://current' })
    expect(bridge.ensureConnection).toHaveBeenCalledTimes(2)
  })

  it('preserves the typed Electron error and releases the scope for retry', async () => {
    const failure = Object.assign(new Error('remote unavailable'), {
      data: { attemptId: 3, code: 'remote_unreachable', retryable: true }
    })

    const recovered = connection('recovered')

    const { bridge } = installBridge({
      ensureConnection: vi.fn().mockRejectedValueOnce(failure).mockResolvedValueOnce(recovered)
    })

    await expect(ensureDesktopConnection({ profile: 'default' })).rejects.toBe(failure)
    await expect(ensureDesktopConnection({ profile: 'default' })).resolves.toBe(recovered)
    expect(bridge.ensureConnection).toHaveBeenCalledTimes(2)
  })

  it('falls back to legacy ensure calls but keeps inspection side-effect free', async () => {
    const local = connection('legacy-local')
    const office = connection('legacy-office')
    const getConnection = vi.fn().mockResolvedValue(local)
    const getConnectionFor = vi.fn().mockResolvedValue(office)

    const { bridge } = installBridge({
      ensureConnection: undefined,
      getConnection,
      getConnectionFor,
      inspectConnection: undefined,
      onConnectionLifecycle: undefined
    })

    await expect(ensureDesktopConnection({ profile: 'work' })).resolves.toBe(local)
    await expect(ensureDesktopConnection({ connectionId: 'office', profile: 'work' })).resolves.toBe(office)
    await expect(inspectDesktopConnection({ profile: 'work' })).resolves.toMatchObject({ state: 'absent' })
    expect(getConnection).toHaveBeenCalledTimes(1)
    expect(getConnectionFor).toHaveBeenCalledTimes(1)
    expect(bridge.ensureConnection).toBeUndefined()
  })
})
