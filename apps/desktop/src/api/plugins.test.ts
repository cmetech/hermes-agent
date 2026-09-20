import { afterEach, describe, expect, it, vi } from 'vitest'

import { setApiRequestConnection, setApiRequestProfile } from '@/hermes'
import { _resetDesktopConnectionForTests } from '@/lib/desktop-connection'

import { activeConnection } from './plugins'

function deferred<T>() {
  let resolve!: (value: T) => void

  const promise = new Promise<T>(onResolve => {
    resolve = onResolve
  })

  return { promise, resolve }
}

describe('activeConnection connection authority', () => {
  afterEach(() => {
    setApiRequestConnection(null)
    setApiRequestProfile(null)
    _resetDesktopConnectionForTests()
    Reflect.deleteProperty(window, 'hermesDesktop')
    vi.useRealTimers()
  })

  it('waits beyond the retired renderer deadline for Electron to finish the authoritative attempt', async () => {
    vi.useFakeTimers()
    setApiRequestProfile('coder')
    const descriptor = { baseUrl: 'http://127.0.0.1:5151', connectionGeneration: 3 }
    const connection = deferred<typeof descriptor>()
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: {
        ensureConnection: vi.fn(() => connection.promise),
        getConnection: vi.fn()
      }
    })

    let settled = false

    const pending = activeConnection().finally(() => {
      settled = true
    })

    await vi.advanceTimersByTimeAsync(25_000)
    expect(settled).toBe(false)
    connection.resolve(descriptor)
    await expect(pending).resolves.toBe(descriptor)
  })

  it('preserves Electron terminal errors and releases the scope for retry', async () => {
    setApiRequestConnection('gw-tailscale')
    setApiRequestProfile('research')

    const failure = Object.assign(new Error('remote unavailable'), {
      data: { attemptId: 8, code: 'remote_unreachable', retryable: true }
    })

    const descriptor = {
      baseUrl: 'https://gateway.example',
      connectionGeneration: 9,
      connectionId: 'gw-tailscale'
    }

    const ensureConnection = vi.fn().mockRejectedValueOnce(failure).mockResolvedValueOnce(descriptor)

    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: { ensureConnection, getConnection: vi.fn() }
    })

    await expect(activeConnection()).rejects.toBe(failure)
    await expect(activeConnection()).resolves.toBe(descriptor)
    expect(ensureConnection).toHaveBeenCalledTimes(2)
  })

  it('still bounds a wedged older preload bridge', async () => {
    vi.useFakeTimers()
    setApiRequestProfile('coder')
    Object.defineProperty(window, 'hermesDesktop', {
      configurable: true,
      value: { getConnection: vi.fn(() => new Promise(() => undefined)) }
    })

    const assertion = expect(activeConnection()).rejects.toThrow(
      'Timed out waiting for a legacy desktop connection bridge'
    )

    await vi.advanceTimersByTimeAsync(20_000)
    await assertion
  })
})
