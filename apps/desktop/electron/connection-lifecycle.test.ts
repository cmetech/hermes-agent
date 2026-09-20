import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { BackendDialClaims } from './backend-dial-claim'
import { DEFAULT_BACKEND_READY_TIMEOUT_MS } from './backend-health'
import { DEFAULT_PORT_ANNOUNCE_TIMEOUT_MS } from './backend-ready'
import { createConnectionGenerationRegistry, ensureConnectionGenerationRoute } from './connection-generation'
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
import { connectionPreloadPolicy, resolveConnectionLifecyclePolicy } from './connection-lifecycle-policy'
import { DEFAULT_CONNECT_TIMEOUT_MS as DEFAULT_GATEWAY_WS_CONNECT_TIMEOUT_MS } from './gateway-ws-probe'
import { runPrimaryBackendStartup } from './primary-backend-startup'
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
        DEFAULT_PORT_ANNOUNCE_TIMEOUT_MS + DEFAULT_BACKEND_READY_TIMEOUT_MS + DEFAULT_GATEWAY_WS_CONNECT_TIMEOUT_MS
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

    const extended = resolveConnectionLifecyclePolicy({ HERMES_DESKTOP_PORT_ANNOUNCE_TIMEOUT_MS: '180000' }, 'win32')

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
  dial: (
    scope: { connectionId: string | null; profile: string },
    report: (phase: any) => void
  ) => Promise<TestConnection>,
  options: { reuseReady?: (connection: TestConnection) => boolean; retire?: (scope: any) => Promise<void> } = {}
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
    setTimer: (callback, ms) => setTimeout(callback, ms),
    ...options
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

  it('revalidates ready remotes through the dial authority and shares concurrent recovery', async () => {
    const recovery = deferred<TestConnection>()
    let dials = 0

    const { coordinator } = lifecycleHarness(
      () => (++dials === 1 ? Promise.resolve({ baseUrl: 'https://dead', token: 'old' }) : recovery.promise),
      { reuseReady: () => false }
    )

    const scope = { connectionId: 'ssh', profile: 'default' }
    await coordinator.ensure(scope)
    const first = coordinator.ensure(scope)
    const second = coordinator.ensure(scope)
    expect(first).toBe(second)
    recovery.resolve({ baseUrl: 'https://recovered', token: 'fresh' })
    await expect(first).resolves.toMatchObject({ baseUrl: 'https://recovered' })
  })

  it.each(['choice', 'update', 'install'] as const)(
    'excludes %s preparation while retaining the remaining active deadline',
    async stage => {
      const preparation = deferred<any>()
      const launch = deferred<TestConnection>()
      const timeout = resolveConnectionLifecyclePolicy({}).attemptTimeoutMs

      const { coordinator } = lifecycleHarness(async (_scope, report) => {
        await new Promise(resolve => setTimeout(resolve, 1_000))
        await runPrimaryBackendStartup({
          connectRemote: async () => {
            throw new Error('unexpected remote')
          },
          ensureLocalRuntime: () => (stage === 'install' ? preparation.promise : Promise.resolve({})),
          prepareLocalBackend: () => ({}),
          resolveRemote: async () => null,
          waitForDecision: () => (stage === 'choice' ? preparation.promise : Promise.resolve('continue-local')),
          waitForLocalStart: () => (stage === 'update' ? preparation.promise : Promise.resolve()),
          requiresInstall: () => stage === 'install',
          reportPreparation: preparing => report(preparing ? 'preparing' : 'launch')
        })

        return launch.promise
      })

      const pending = coordinator.ensure({ profile: 'default' })
      const rejected = expect(pending).rejects.toMatchObject({ data: { code: 'attempt_timeout' } })
      await vi.advanceTimersByTimeAsync(timeout * 2)
      expect(coordinator.inspect({}).state).toBe('starting')
      preparation.resolve(stage === 'choice' ? 'continue-local' : {})
      await vi.advanceTimersByTimeAsync(timeout - 1_001)
      expect(coordinator.inspect({}).state).toBe('starting')
      await vi.advanceTimersByTimeAsync(1)
      await rejected
    }
  )

  it('retires an underlying dial claim before retry and fences late settlement', async () => {
    const claims = new BackendDialClaims()
    const old = deferred<TestConnection>()
    const retirement = deferred<void>()
    let attempts = 0
    const fresh = { baseUrl: 'http://fresh', token: 'fresh' }

    const { coordinator } = lifecycleHarness(
      () => claims.run('work', () => (++attempts === 1 ? old.promise : Promise.resolve(fresh))),
      {
        retire: () => claims.retire('work', () => retirement.promise)
      }
    )

    const failed = expect(coordinator.ensure({ profile: 'work' })).rejects.toMatchObject({
      data: { code: 'attempt_timeout' }
    })

    await vi.advanceTimersByTimeAsync(resolveConnectionLifecyclePolicy({}).attemptTimeoutMs)
    await failed
    const retry = coordinator.ensure({ profile: 'work' })
    expect(attempts).toBe(1)
    retirement.resolve()
    await expect(retry).resolves.toBe(fresh)
    old.resolve({ baseUrl: 'http://obsolete', token: 'old' })
    await Promise.resolve()
    await expect(coordinator.ensure({ profile: 'work' })).resolves.toBe(fresh)
    expect(attempts).toBe(2)
  })

  it('coalesces a lease-held API ensure with renderer startup without losing the captured fence', async () => {
    const authority = createConnectionGenerationRegistry()
    const routeKey = '["office","work"]'
    const captured = { routeKey, lease: authority.captureRoute(routeKey) }
    const claim = authority.begin('pool:office')
    const pending = deferred<TestConnection & { connectionGeneration: number }>()
    const { coordinator } = lifecycleHarness(() => pending.promise)
    const scope = { connectionId: 'office', profile: 'work' }

    const api = ensureConnectionGenerationRoute(authority, routeKey,
      async () => await coordinator.ensure(scope) as TestConnection & { connectionGeneration: number }, captured)

    const renderer = coordinator.ensure(scope)
    authority.retireRoute(routeKey)
    pending.resolve(authority.publish(claim, { baseUrl: 'https://office', token: 'safe' }))
    await expect(api).rejects.toThrow('marketplace_connection_generation_changed')
    await expect(renderer).resolves.toMatchObject({ baseUrl: 'https://office' })
    expect(authority.current(routeKey)).toBeUndefined()
  })

  it.each(['invalidated', 'timed out'])('never dials a retry %s while retirement was pending', async outcome => {
    const retirement = deferred<void>()
    let attempts = 0

    const { coordinator } = lifecycleHarness(
      () => {
        attempts += 1

        return new Promise(() => {})
      },
      { retire: () => retirement.promise }
    )

    const scope = { profile: 'work' }
    const first = coordinator.ensure(scope).catch(error => error)
    await vi.advanceTimersByTimeAsync(resolveConnectionLifecyclePolicy({}).attemptTimeoutMs)
    await first
    const retry = coordinator.ensure(scope).catch(error => error)

    if (outcome === 'invalidated') {
      coordinator.invalidate(scope)
    } else {
      await vi.advanceTimersByTimeAsync(resolveConnectionLifecyclePolicy({}).attemptTimeoutMs)
    }

    await retry
    retirement.resolve()
    await vi.advanceTimersByTimeAsync(0)
    expect(attempts).toBe(1)
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

    const { coordinator, logs, snapshots } = lifecycleHarness(async (_scope, report) => {
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
    expect(logs).toEqual([
      {
        attemptId: 1,
        code: null,
        elapsedMs: 0,
        phase: 'health',
        scope: { connectionId: null, profile: 'default' },
        state: 'ready'
      }
    ])
  })

  it('classifies terminal errors and emits exactly one credential-free terminal log', async () => {
    const { coordinator, logs } = lifecycleHarness(async (_scope, report) => {
      report('launch')
      vi.setSystemTime(1_250)
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
      elapsedMs: 250,
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

  it('replays already-active primary preparation and keeps preload alive until startup resumes', async () => {
    const choice = deferred<any>()
    const ready = { baseUrl: 'http://ready', token: 'safe' }

    const { coordinator } = lifecycleHarness(async (_scope, report) => {
      await runPrimaryBackendStartup({
        connectRemote: async () => ready,
        ensureLocalRuntime: async backend => backend,
        prepareLocalBackend: () => ({}),
        resolveRemote: async () => null,
        waitForDecision: () => choice.promise,
        waitForLocalStart: async () => {},
        reportPreparation: preparing => report(preparing ? 'preparing' : 'launch')
      })

      return ready
    })

    const bridge = createConnectionLifecycleBridge({
      coordinator,
      normalizeScope: scope => normalizeMainConnectionScope(scope, 'work', 'local')
    })

    const boot = bridge.ensure({})
    await vi.advanceTimersByTimeAsync(0)
    const policy = resolveConnectionLifecyclePolicy({})

    const pending = invokeWithConnectionWatchdog(() => boot, {}, policy.preloadWatchdogMs, {
      inspect: async () => bridge.inspect({}),
      subscribe: () => () => {},
      checkTimeoutMs: policy.ipcDeliveryMarginMs
    })

    void pending.catch(() => {})
    await vi.advanceTimersByTimeAsync(policy.preloadWatchdogMs * 3)
    choice.resolve('continue-local')
    await expect(pending).resolves.toBe(ready)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('carries the preparation inspection budget through the serialized main-to-preload policy', async () => {
    const wirePolicy = JSON.parse(JSON.stringify(connectionPreloadPolicy(resolveConnectionLifecyclePolicy({}))))
    const scope = { connectionId: null, profile: 'work' }
    const ready = { baseUrl: 'http://ready', token: 'safe' }
    const result = deferred<{ ok: true; connection: TestConnection }>()
    let inspections = 0

    const pending = invokeWithConnectionWatchdog(() => result.promise, scope, Number(wirePolicy.preloadWatchdogMs), {
      inspect: async () => {
        if (++inspections > 1) {
          await new Promise(resolve => setTimeout(resolve, 5))
        }

        return { attemptId: 1, elapsedMs: 0, phase: 'preparing', scope, state: 'starting' }
      },
      subscribe: () => () => {},
      checkTimeoutMs: Number(wirePolicy.ipcDeliveryMarginMs)
    })

    void pending.catch(() => {})
    await vi.advanceTimersByTimeAsync(15_010)
    result.resolve({ ok: true, connection: ready })
    await expect(pending).resolves.toBe(ready)
    expect(inspections).toBeGreaterThan(1)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('bounds a main process that stops answering inspection during preparation', async () => {
    const scope = { connectionId: null, profile: 'work' }
    let inspections = 0

    const pending = invokeWithConnectionWatchdog(() => new Promise(() => {}), scope, 100, {
      inspect: () =>
        ++inspections === 1
          ? Promise.resolve({ attemptId: 1, elapsedMs: 0, phase: 'preparing', scope, state: 'starting' })
          : new Promise(() => {}),
      subscribe: () => () => {},
      checkTimeoutMs: 20
    })

    const rejected = expect(pending).rejects.toMatchObject({ data: { code: 'ipc_timeout' } })
    await vi.advanceTimersByTimeAsync(40)
    await rejected
    expect(vi.getTimerCount()).toBe(0)
  })

  it('cleans up inspection timers when ensure settles first', async () => {
    const connection = { baseUrl: 'http://ready', token: 'safe' }
    await expect(
      invokeWithConnectionWatchdog(async () => ({ ok: true, connection }), {}, 100, {
        inspect: () => new Promise(() => {}),
        subscribe: () => () => {},
        checkTimeoutMs: 20
      })
    ).resolves.toBe(connection)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('does not shorten the active watchdog when the initial inspection is slow', async () => {
    const ready = { baseUrl: 'http://ready', token: 'safe' }
    const result = deferred<{ ok: true; connection: TestConnection }>()

    const pending = invokeWithConnectionWatchdog(() => result.promise, {}, 100, {
      inspect: () => new Promise(() => {}),
      subscribe: () => () => {},
      checkTimeoutMs: 20
    })

    void pending.catch(() => {})
    await vi.advanceTimersByTimeAsync(50)
    result.resolve({ ok: true, connection: ready })
    await expect(pending).resolves.toBe(ready)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('ignores other scopes and does not renew the active preload budget on progress', async () => {
    let listener!: (snapshot: any) => void
    const scope = { connectionId: 'office', profile: 'work' }

    const pending = invokeWithConnectionWatchdog(() => new Promise(() => {}), scope, 100, {
      inspect: async () => ({ attemptId: 1, elapsedMs: 0, phase: 'launch', scope, state: 'starting' }),
      subscribe: callback => {
        listener = callback

        return () => {}
      },
      checkTimeoutMs: 20
    })

    const rejected = expect(pending).rejects.toMatchObject({ data: { code: 'ipc_timeout' } })
    await vi.advanceTimersByTimeAsync(50)
    listener({
      attemptId: 2,
      elapsedMs: 0,
      phase: 'preparing',
      scope: { ...scope, profile: 'other' },
      state: 'starting'
    })
    listener({ attemptId: 1, elapsedMs: 50, phase: 'health', scope, state: 'starting' })
    await vi.advanceTimersByTimeAsync(50)
    await rejected
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
