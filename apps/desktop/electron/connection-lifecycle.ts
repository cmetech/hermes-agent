import type { ConnectionLifecyclePolicy } from './connection-lifecycle-policy'

export interface DesktopConnectionScopeInput {
  connectionId?: null | string
  profile?: null | string
}

export interface DesktopConnectionScope {
  connectionId: null | string
  profile: string
}

export type DesktopConnectionPhase = 'resolve' | 'launch' | 'port' | 'health' | 'remote'
export type DesktopConnectionState = 'absent' | 'starting' | 'ready' | 'failed'

export type DesktopConnectionErrorCode =
  | 'launch_failed'
  | 'port_timeout'
  | 'health_timeout'
  | 'remote_unreachable'
  | 'auth_required'
  | 'missing_connection'
  | 'missing_profile'
  | 'invalidated'
  | 'attempt_timeout'
  | 'ipc_timeout'

export interface ClassifiedConnectionError {
  code: DesktopConnectionErrorCode
  message: string
  retryable: boolean
}

export interface DesktopConnectionErrorData extends ClassifiedConnectionError {
  attemptId: number
  elapsedMs: number
  phase: DesktopConnectionPhase
  scope: DesktopConnectionScope
}

export interface DesktopConnectionSnapshot<TConnection> {
  attemptId: number | null
  connection?: TConnection
  elapsedMs: number
  error?: DesktopConnectionErrorData
  phase: DesktopConnectionPhase | null
  scope: DesktopConnectionScope
  state: DesktopConnectionState
}

export interface ConnectionLifecycleTerminalLog {
  attemptId: number
  code: DesktopConnectionErrorCode | null
  elapsedMs: number
  phase: DesktopConnectionPhase
  scope: DesktopConnectionScope
  state: 'failed' | 'ready'
}

export class ConnectionLifecycleError extends Error {
  readonly data: DesktopConnectionErrorData

  constructor(data: DesktopConnectionErrorData) {
    super(data.message)
    this.name = 'ConnectionLifecycleError'
    this.data = data
  }
}

type TimerHandle = ReturnType<typeof setTimeout>

interface ConnectionLifecycleDependencies<TConnection> {
  classifyError: (error: unknown) => ClassifiedConnectionError
  clearTimer: (timer: TimerHandle) => void
  clock: () => number
  dial: (
    scope: DesktopConnectionScope,
    reportPhase: (phase: DesktopConnectionPhase) => void
  ) => Promise<TConnection> | TConnection
  log: (record: ConnectionLifecycleTerminalLog) => void
  policy: ConnectionLifecyclePolicy
  publish: (snapshot: DesktopConnectionSnapshot<TConnection>) => void
  setTimer: (callback: () => void, ms: number) => TimerHandle
}

interface ConnectionLifecycleEntry<TConnection> {
  attemptId: number
  connection?: TConnection
  error?: DesktopConnectionErrorData
  inFlight?: Promise<TConnection>
  invalidate?: (error: ConnectionLifecycleError) => void
  phase: DesktopConnectionPhase
  scope: DesktopConnectionScope
  settledAt?: number
  startedAt: number
  state: Exclude<DesktopConnectionState, 'absent'>
  timer?: TimerHandle
}

export function normalizeDesktopConnectionScope(scope: DesktopConnectionScopeInput): DesktopConnectionScope {
  const connectionId = String(scope.connectionId ?? '').trim()
  const profile = String(scope.profile ?? '').trim()

  return {
    connectionId: connectionId || null,
    profile: profile || 'default'
  }
}

export function desktopConnectionScopeKey(scope: DesktopConnectionScopeInput): string {
  const normalized = normalizeDesktopConnectionScope(scope)

  return JSON.stringify([normalized.connectionId, normalized.profile])
}

export class ConnectionLifecycleCoordinator<TConnection> {
  readonly #dependencies: ConnectionLifecycleDependencies<TConnection>
  readonly #entries = new Map<string, ConnectionLifecycleEntry<TConnection>>()
  #nextAttemptId = 1

  constructor(dependencies: ConnectionLifecycleDependencies<TConnection>) {
    this.#dependencies = dependencies
  }

  inspect(scopeInput: DesktopConnectionScopeInput): DesktopConnectionSnapshot<TConnection> {
    const scope = normalizeDesktopConnectionScope(scopeInput)
    const entry = this.#entries.get(desktopConnectionScopeKey(scope))

    if (!entry) {
      return {
        attemptId: null,
        elapsedMs: 0,
        phase: null,
        scope,
        state: 'absent'
      }
    }

    return this.#snapshot(entry)
  }

  ensure(scopeInput: DesktopConnectionScopeInput): Promise<TConnection> {
    const scope = normalizeDesktopConnectionScope(scopeInput)
    const key = desktopConnectionScopeKey(scope)
    const current = this.#entries.get(key)

    if (current?.state === 'ready' && current.connection !== undefined) {
      return Promise.resolve(current.connection)
    }

    if (current?.state === 'starting' && current.inFlight) {
      return current.inFlight
    }

    const entry: ConnectionLifecycleEntry<TConnection> = {
      attemptId: this.#nextAttemptId++,
      phase: 'resolve',
      scope,
      startedAt: this.#dependencies.clock(),
      state: 'starting'
    }

    this.#entries.set(key, entry)
    this.#publish(entry)

    const reportPhase = (phase: DesktopConnectionPhase) => {
      if (this.#entries.get(key) !== entry || entry.state !== 'starting' || entry.phase === phase) {
        return
      }

      entry.phase = phase
      this.#publish(entry)
    }

    let dialPromise: Promise<TConnection>

    try {
      dialPromise = Promise.resolve(this.#dependencies.dial(scope, reportPhase))
    } catch (error) {
      dialPromise = Promise.reject(error)
    }

    const timeoutPromise = new Promise<never>((_resolve, reject) => {
      entry.timer = this.#dependencies.setTimer(() => {
        reject(
          new ConnectionLifecycleError(
            this.#errorData(entry, {
              code: 'attempt_timeout',
              message: 'Hermes connection attempt exceeded its Electron lifecycle deadline.',
              retryable: true
            })
          )
        )
      }, this.#dependencies.policy.attemptTimeoutMs)
    })

    const invalidationPromise = new Promise<never>((_resolve, reject) => {
      entry.invalidate = reject
    })

    const inFlight = Promise.race([dialPromise, timeoutPromise, invalidationPromise]).then(
      connection => {
        if (this.#entries.get(key) !== entry || entry.state !== 'starting') {
          throw new ConnectionLifecycleError(
            this.#errorData(entry, {
              code: 'invalidated',
              message: 'Hermes connection attempt was invalidated.',
              retryable: true
            })
          )
        }

        this.#clearTimer(entry)
        entry.connection = connection
        entry.inFlight = undefined
        entry.invalidate = undefined
        entry.settledAt = this.#dependencies.clock()
        entry.state = 'ready'
        this.#publish(entry)
        this.#logTerminal(entry, null)

        return connection
      },
      error => {
        if (this.#entries.get(key) !== entry || entry.state !== 'starting') {
          throw error
        }

        this.#clearTimer(entry)

        const lifecycleError =
          error instanceof ConnectionLifecycleError
            ? error
            : new ConnectionLifecycleError(this.#errorData(entry, this.#dependencies.classifyError(error)))

        entry.error = lifecycleError.data
        entry.inFlight = undefined
        entry.invalidate = undefined
        entry.settledAt = this.#dependencies.clock()
        entry.state = 'failed'
        this.#publish(entry)
        this.#logTerminal(entry, lifecycleError.data.code)

        throw lifecycleError
      }
    )

    entry.inFlight = inFlight

    return inFlight
  }

  invalidate(scopeInput: DesktopConnectionScopeInput): boolean {
    const scope = normalizeDesktopConnectionScope(scopeInput)
    const key = desktopConnectionScopeKey(scope)
    const entry = this.#entries.get(key)

    if (!entry) {
      return false
    }

    if (entry.state !== 'starting') {
      this.#entries.delete(key)
      this.#dependencies.publish({
        attemptId: null,
        elapsedMs: 0,
        phase: null,
        scope,
        state: 'absent'
      })

      return true
    }

    this.#clearTimer(entry)

    const error = new ConnectionLifecycleError(
      this.#errorData(entry, {
        code: 'invalidated',
        message: 'Hermes connection attempt was invalidated.',
        retryable: true
      })
    )

    entry.error = error.data
    entry.inFlight = undefined
    entry.settledAt = this.#dependencies.clock()
    entry.state = 'failed'
    this.#publish(entry)
    this.#logTerminal(entry, error.data.code)
    const reject = entry.invalidate

    entry.invalidate = undefined
    reject?.(error)

    return true
  }

  #clearTimer(entry: ConnectionLifecycleEntry<TConnection>): void {
    if (entry.timer === undefined) {
      return
    }

    this.#dependencies.clearTimer(entry.timer)
    entry.timer = undefined
  }

  #elapsed(entry: ConnectionLifecycleEntry<TConnection>): number {
    return Math.max(0, (entry.settledAt ?? this.#dependencies.clock()) - entry.startedAt)
  }

  #errorData(
    entry: ConnectionLifecycleEntry<TConnection>,
    error: ClassifiedConnectionError
  ): DesktopConnectionErrorData {
    return {
      ...error,
      attemptId: entry.attemptId,
      elapsedMs: this.#elapsed(entry),
      phase: entry.phase,
      scope: { ...entry.scope }
    }
  }

  #logTerminal(entry: ConnectionLifecycleEntry<TConnection>, code: DesktopConnectionErrorCode | null): void {
    this.#dependencies.log({
      attemptId: entry.attemptId,
      code,
      elapsedMs: this.#elapsed(entry),
      phase: entry.phase,
      scope: { ...entry.scope },
      state: entry.state === 'ready' ? 'ready' : 'failed'
    })
  }

  #publish(entry: ConnectionLifecycleEntry<TConnection>): void {
    this.#dependencies.publish(this.#snapshot(entry))
  }

  #snapshot(entry: ConnectionLifecycleEntry<TConnection>): DesktopConnectionSnapshot<TConnection> {
    return {
      attemptId: entry.attemptId,
      ...(entry.connection === undefined ? {} : { connection: entry.connection }),
      elapsedMs: this.#elapsed(entry),
      ...(entry.error === undefined ? {} : { error: { ...entry.error, scope: { ...entry.error.scope } } }),
      phase: entry.phase,
      scope: { ...entry.scope },
      state: entry.state
    }
  }
}
