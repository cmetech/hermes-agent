import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { DEFAULT_BACKEND_READY_TIMEOUT_MS } from './backend-health'
import { DEFAULT_PORT_ANNOUNCE_TIMEOUT_MS } from './backend-ready'
import {
  ConnectionLifecycleCoordinator,
  ConnectionLifecycleError,
  type ConnectionLifecycleTerminalLog,
  type DesktopConnectionSnapshot
} from './connection-lifecycle'
import {
  CONNECTION_LIFECYCLE_CHANNEL,
  createConnectionLifecycleBridge,
  invokeWithConnectionWatchdog,
  normalizeMainConnectionScope,
  sanitizeConnectionErrorMessage,
  subscribeConnectionLifecycle
} from './connection-lifecycle-bridge'
import { resolveConnectionLifecyclePolicy } from './connection-lifecycle-policy'
import { DEFAULT_CONNECT_TIMEOUT_MS as DEFAULT_GATEWAY_WS_CONNECT_TIMEOUT_MS } from './gateway-ws-probe'
import {
  DEFAULT_CONNECT_TIMEOUT_MS as DEFAULT_SSH_CONNECT_TIMEOUT_MS,
  DEFAULT_EXEC_TIMEOUT_MS as DEFAULT_SSH_EXEC_TIMEOUT_MS,
  DEFAULT_FORWARD_TIMEOUT_MS as DEFAULT_SSH_FORWARD_TIMEOUT_MS
} from './ssh-connection'

describe('connection lifecycle deadline policy', () => {
  it('uses one OS-neutral policy whose local budget covers every cold-start stage', () => {
    const policies = (['win32', 'darwin', 'linux'] as const).map(platform =>
      resolveConnectionLifecyclePolicy({}, platform)
    )

    expect(policies[0]).toEqual(policies[1])
    expect(policies[1]).toEqual(policies[2])

    for (const policy of policies) {
      expect(policy.localAttemptTimeoutMs).toBeGreaterThanOrEqual(
        DEFAULT_PORT_ANNOUNCE_TIMEOUT_MS +
          DEFAULT_BACKEND_READY_TIMEOUT_MS +
          DEFAULT_GATEWAY_WS_CONNECT_TIMEOUT_MS
      )
      expect(policy.attemptTimeoutMs).toBeGreaterThanOrEqual(policy.localAttemptTimeoutMs)
    }
  })

  it('covers the existing bounded SSH stages without introducing a Windows-only allowance', () => {
    const policy = resolveConnectionLifecyclePolicy({}, 'win32')

    expect(policy.remoteAttemptTimeoutMs).toBeGreaterThanOrEqual(
      DEFAULT_SSH_CONNECT_TIMEOUT_MS + DEFAULT_SSH_EXEC_TIMEOUT_MS + DEFAULT_SSH_FORWARD_TIMEOUT_MS
    )
    expect(policy.attemptTimeoutMs).toBeGreaterThanOrEqual(policy.remoteAttemptTimeoutMs)
  })

  it('keeps the preload watchdog strictly outside every valid Electron attempt', () => {
    const policy = resolveConnectionLifecyclePolicy({}, 'linux')

    expect(policy.preloadWatchdogMs).toBe(policy.attemptTimeoutMs + policy.ipcDeliveryMarginMs)
    expect(policy.ipcDeliveryMarginMs).toBeGreaterThan(0)
    expect(policy.preloadWatchdogMs).toBeGreaterThan(policy.localAttemptTimeoutMs)
    expect(policy.preloadWatchdogMs).toBeGreaterThan(policy.remoteAttemptTimeoutMs)
  })

  it('derives the Electron and preload bounds from the existing port-announcement override', () => {
    const baseline = resolveConnectionLifecyclePolicy({}, 'win32')

    const extended = resolveConnectionLifecyclePolicy(
      { HERMES_DESKTOP_PORT_ANNOUNCE_TIMEOUT_MS: '180000' },
      'win32'
    )

    expect(extended.portAnnounceTimeoutMs).toBe(180_000)
    expect(extended.localAttemptTimeoutMs).toBeGreaterThan(baseline.localAttemptTimeoutMs)
    expect(extended.attemptTimeoutMs).toBeGreaterThan(baseline.attemptTimeoutMs)
    expect(extended.preloadWatchdogMs).toBe(extended.attemptTimeoutMs + extended.ipcDeliveryMarginMs)
  })
})

interface TestConnection {
  baseUrl: string
  token: string
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void

  const promise = new Promise<T>((onResolve, onReject) => {
    resolve = onResolve
    reject = onReject
  })

  return { promise, reject, resolve }
}

function lifecycleHarness(
  dial: (scope: { connectionId: string | null; profile: string }, report: (phase: any) => void) => Promise<TestConnection>
) {
  const snapshots: DesktopConnectionSnapshot<TestConnection>[] = []
  const logs: ConnectionLifecycleTerminalLog[] = []

  const coordinator = new ConnectionLifecycleCoordinator<TestConnection>({
    classifyError: error => ({
      code: 'launch_failed',
      message: error instanceof Error ? error.message : String(error),
      retryable: true
    }),
    clearTimer: timer => clearTimeout(timer),
    clock: () => Date.now(),
    dial,
    log: record => logs.push(record),
    policy: resolveConnectionLifecyclePolicy({}, 'win32'),
    publish: snapshot => snapshots.push(snapshot),
    setTimer: (callback, ms) => setTimeout(callback, ms)
  })

  return { coordinator, logs, snapshots }
}

describe('ConnectionLifecycleCoordinator', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('lets a healthy 53-second Windows cold start finish instead of applying the old renderer deadline', async () => {
    const connection = { baseUrl: 'http://127.0.0.1:43123', token: 'secret' }

    const { coordinator } = lifecycleHarness(
      () => new Promise(resolve => setTimeout(() => resolve(connection), 53_000))
    )

    const pending = coordinator.ensure({ profile: 'default' })
    await vi.advanceTimersByTimeAsync(53_000)

    await expect(pending).resolves.toBe(connection)
    expect(coordinator.inspect({ profile: 'default' }).state).toBe('ready')
  })

  it('bounds a genuinely never-settling dial at the Electron attempt deadline and releases it for retry', async () => {
    const firstDial = deferred<TestConnection>()
    const recovered = { baseUrl: 'http://127.0.0.1:43124', token: 'fresh' }
    let attempts = 0

    const { coordinator } = lifecycleHarness(() => {
      attempts += 1

      return attempts === 1 ? firstDial.promise : Promise.resolve(recovered)
    })

    const timeoutMs = resolveConnectionLifecyclePolicy({}, 'win32').attemptTimeoutMs

    const first = coordinator.ensure({ profile: 'work' })

    const rejected = expect(first).rejects.toMatchObject({
      data: { code: 'attempt_timeout', phase: 'resolve', retryable: true }
    })

    await vi.advanceTimersByTimeAsync(timeoutMs)
    await rejected

    await expect(coordinator.ensure({ profile: 'work' })).resolves.toBe(recovered)
    expect(attempts).toBe(2)
  })

  it('shares one promise and attempt for concurrent callers in the same normalized scope', async () => {
    const pendingDial = deferred<TestConnection>()
    let dials = 0

    const { coordinator } = lifecycleHarness(() => {
      dials += 1

      return pendingDial.promise
    })

    const first = coordinator.ensure({ connectionId: ' office ', profile: ' work ' })
    const second = coordinator.ensure({ connectionId: 'office', profile: 'work' })

    expect(second).toBe(first)
    expect(dials).toBe(1)
    expect(coordinator.inspect({ connectionId: 'office', profile: 'work' }).attemptId).toBe(1)

    pendingDial.resolve({ baseUrl: 'https://office.example', token: 'secret' })
    await first
  })

  it('keeps different profile and connection scopes independent', async () => {
    const seen: string[] = []

    const { coordinator } = lifecycleHarness(async scope => {
      seen.push(`${scope.connectionId ?? 'primary'}:${scope.profile}`)

      return { baseUrl: `https://${scope.connectionId ?? 'local'}/${scope.profile}`, token: 'secret' }
    })

    const [primary, registry, named] = await Promise.all([
      coordinator.ensure({ profile: 'default' }),
      coordinator.ensure({ connectionId: 'office', profile: 'default' }),
      coordinator.ensure({ profile: 'research' })
    ])

    expect(seen.sort()).toEqual(['office:default', 'primary:default', 'primary:research'])
    expect(primary.baseUrl).not.toBe(registry.baseUrl)
    expect(named.baseUrl).not.toBe(primary.baseUrl)
  })

  it('returns a ready descriptor without dialing or scheduling another deadline', async () => {
    const connection = { baseUrl: 'http://127.0.0.1:43125', token: 'secret' }
    let dials = 0

    const { coordinator } = lifecycleHarness(async () => {
      dials += 1

      return connection
    })

    await coordinator.ensure({ profile: 'default' })
    const cached = coordinator.ensure({ profile: 'default' })

    await expect(cached).resolves.toBe(connection)
    expect(dials).toBe(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('invalidates the matching attempt immediately and ignores its late success', async () => {
    const pendingDial = deferred<TestConnection>()
    const { coordinator, snapshots } = lifecycleHarness(() => pendingDial.promise)
    const pending = coordinator.ensure({ connectionId: 'office', profile: 'research' })

    expect(coordinator.invalidate({ connectionId: 'office', profile: 'research' })).toBe(true)
    await expect(pending).rejects.toMatchObject({ data: { code: 'invalidated', retryable: true } })

    pendingDial.resolve({ baseUrl: 'https://stale.example', token: 'do-not-publish' })
    await Promise.resolve()

    expect(coordinator.inspect({ connectionId: 'office', profile: 'research' }).state).toBe('failed')
    expect(snapshots.some(snapshot => snapshot.state === 'ready')).toBe(false)
  })

  it('publishes only discrete phase changes in order and inspection has no side effects', async () => {
    let dials = 0

    const { coordinator, snapshots } = lifecycleHarness(async (_scope, report) => {
      dials += 1
      report('launch')
      report('launch')
      report('port')
      report('health')

      return { baseUrl: 'http://127.0.0.1:43126', token: 'secret' }
    })

    expect(coordinator.inspect({ profile: ' default ' })).toMatchObject({
      attemptId: null,
      phase: null,
      scope: { connectionId: null, profile: 'default' },
      state: 'absent'
    })
    expect(dials).toBe(0)

    await coordinator.ensure({ profile: 'default' })

    expect(snapshots.map(snapshot => `${snapshot.state}:${snapshot.phase}`)).toEqual([
      'starting:resolve',
      'starting:launch',
      'starting:port',
      'starting:health',
      'ready:health'
    ])
  })

  it('classifies terminal errors and emits exactly one credential-free terminal log', async () => {
    const { coordinator, logs } = lifecycleHarness(async (_scope, report) => {
      report('launch')
      throw new Error('spawn refused')
    })

    const failure = coordinator.ensure({ profile: 'default' })
    await expect(failure).rejects.toBeInstanceOf(ConnectionLifecycleError)
    await expect(failure).rejects.toMatchObject({
      data: { code: 'launch_failed', message: 'spawn refused', phase: 'launch', retryable: true }
    })

    expect(logs).toHaveLength(1)
    expect(logs[0]).toMatchObject({
      attemptId: 1,
      code: 'launch_failed',
      phase: 'launch',
      scope: { connectionId: null, profile: 'default' },
      state: 'failed'
    })
    expect(JSON.stringify(logs[0])).not.toContain('token')
    expect(JSON.stringify(logs[0])).not.toContain('baseUrl')
  })
})

describe('connection lifecycle IPC bridge', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(1_000)
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('normalizes primary and registry scopes without conflating their profile defaults', () => {
    expect(
      normalizeMainConnectionScope({ connectionId: null, profile: ' ' }, 'desktop-active', 'registry-primary')
    ).toEqual({ connectionId: null, profile: 'desktop-active' })
    expect(
      normalizeMainConnectionScope({ connectionId: ' office ', profile: ' ' }, 'desktop-active', 'registry-primary')
    ).toEqual({ connectionId: 'office', profile: 'default' })
  })

  it('keeps inspect side-effect free and coalesces two ensure calls onto one dial', async () => {
    const pendingDial = deferred<TestConnection>()
    let dials = 0

    const { coordinator } = lifecycleHarness(() => {
      dials += 1

      return pendingDial.promise
    })

    const bridge = createConnectionLifecycleBridge({
      coordinator,
      normalizeScope: scope => normalizeMainConnectionScope(scope, 'desktop-active', 'registry-primary')
    })

    expect(bridge.inspect({ connectionId: null })).toMatchObject({ state: 'absent' })
    expect(dials).toBe(0)

    const first = bridge.ensure({ connectionId: null })
    const second = bridge.ensure({ connectionId: null, profile: 'desktop-active' })

    expect(dials).toBe(1)
    pendingDial.resolve({ baseUrl: 'http://127.0.0.1:43127', token: 'secret' })
    await expect(Promise.all([first, second])).resolves.toEqual([
      { ok: true, connection: { baseUrl: 'http://127.0.0.1:43127', token: 'secret' } },
      { ok: true, connection: { baseUrl: 'http://127.0.0.1:43127', token: 'secret' } }
    ])
  })

  it('carries a lifecycle failure across IPC as a typed envelope', async () => {
    const { coordinator } = lifecycleHarness(async (_scope, report) => {
      report('remote')
      throw new Error('host unreachable')
    })

    const bridge = createConnectionLifecycleBridge({ coordinator, normalizeScope: scope => scope })

    await expect(bridge.ensure({ connectionId: 'office', profile: 'work' })).resolves.toMatchObject({
      ok: false,
      error: {
        attemptId: 1,
        code: 'launch_failed',
        message: 'host unreachable',
        phase: 'remote',
        retryable: true,
        scope: { connectionId: 'office', profile: 'work' }
      }
    })
  })

  it('bounds and redacts connection error text before it can cross IPC', () => {
    const message = sanitizeConnectionErrorMessage(
      `failed https://host/api/ws?token=fixture-secret Authorization: Bearer other-secret ${'x'.repeat(4_000)}`
    )

    expect(message).not.toContain('fixture-secret')
    expect(message).not.toContain('other-secret')
    expect(message.length).toBeLessThanOrEqual(2_048)
  })

  it('starts the preload watchdog outside the Electron attempt bound and clears it after a fast cached ensure', async () => {
    const policy = resolveConnectionLifecyclePolicy({}, 'win32')
    const never = deferred<{ ok: true; connection: TestConnection }>()

    const pending = invokeWithConnectionWatchdog(
      () => never.promise,
      { connectionId: null, profile: 'default' },
      policy.preloadWatchdogMs
    )

    const rejected = expect(pending).rejects.toMatchObject({
      data: { attemptId: null, code: 'ipc_timeout', retryable: true }
    })

    await vi.advanceTimersByTimeAsync(policy.attemptTimeoutMs)
    expect(vi.getTimerCount()).toBe(1)
    await vi.advanceTimersByTimeAsync(policy.ipcDeliveryMarginMs)
    await rejected

    const connection = { baseUrl: 'http://127.0.0.1:43128', token: 'secret' }
    await expect(
      invokeWithConnectionWatchdog(
        async () => ({ ok: true, connection }),
        { connectionId: null, profile: 'default' },
        policy.preloadWatchdogMs
      )
    ).resolves.toBe(connection)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('delivers discrete sanitized snapshots and removes the listener on unsubscribe', () => {
    const listeners = new Map<string, (event: unknown, payload: unknown) => void>()

    const ipc = {
      on: vi.fn((channel: string, listener: (event: unknown, payload: unknown) => void) => {
        listeners.set(channel, listener)
      }),
      removeListener: vi.fn((channel: string) => listeners.delete(channel))
    }

    const received: unknown[] = []
    const off = subscribeConnectionLifecycle(ipc, snapshot => received.push(snapshot))

    const payload = {
      attemptId: 2,
      elapsedMs: 120,
      phase: 'health',
      scope: { connectionId: null, profile: 'default' },
      state: 'starting'
    }

    listeners.get(CONNECTION_LIFECYCLE_CHANNEL)?.({}, payload)
    listeners.get(CONNECTION_LIFECYCLE_CHANNEL)?.({}, { ...payload, token: 'must-not-cross' })

    expect(received).toEqual([payload])
    off()
    expect(ipc.removeListener).toHaveBeenCalledTimes(1)
    expect(listeners.has(CONNECTION_LIFECYCLE_CHANNEL)).toBe(false)
  })
})
