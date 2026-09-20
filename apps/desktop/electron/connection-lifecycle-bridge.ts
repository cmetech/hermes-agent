import {
  type ConnectionLifecycleCoordinator,
  ConnectionLifecycleError,
  type DesktopConnectionErrorData,
  type DesktopConnectionPhase,
  type DesktopConnectionScope,
  type DesktopConnectionScopeInput,
  type DesktopConnectionSnapshot,
  type DesktopConnectionState
} from './connection-lifecycle'

export const CONNECTION_LIFECYCLE_CHANNEL = 'hermes:connection:lifecycle'
const MAX_CONNECTION_ERROR_MESSAGE_LENGTH = 2_048

export type ConnectionEnsureEnvelope<TConnection> =
  | { ok: true; connection: TConnection }
  | { ok: false; error: DesktopConnectionErrorData }

export type PublicConnectionLifecycleSnapshot = Omit<DesktopConnectionSnapshot<never>, 'connection'>

export function sanitizeConnectionErrorMessage(value: unknown): string {
  return String(value)
    .replace(/([?&](?:access_?token|api_?key|token)=)[^&\s]+/gi, '$1[redacted]')
    .replace(/(authorization\s*:\s*(?:bearer|token)\s+)[^\s,;]+/gi, '$1[redacted]')
    .slice(0, MAX_CONNECTION_ERROR_MESSAGE_LENGTH)
}

interface ConnectionLifecycleBridgeOptions<TConnection> {
  coordinator: ConnectionLifecycleCoordinator<TConnection>
  normalizeScope: (scope: DesktopConnectionScopeInput) => DesktopConnectionScopeInput
  serializeConnection?: (connection: TConnection) => TConnection
}

export function normalizeMainConnectionScope(
  scope: DesktopConnectionScopeInput,
  primaryProfile: string,
  _registryPrimary: string
): DesktopConnectionScope {
  const connectionId = String(scope.connectionId ?? '').trim()
  const profile = String(scope.profile ?? '').trim()

  return {
    connectionId: connectionId || null,
    profile: profile || (connectionId ? 'default' : primaryProfile)
  }
}

export function createConnectionLifecycleBridge<TConnection>({
  coordinator,
  normalizeScope,
  serializeConnection = connection => connection
}: ConnectionLifecycleBridgeOptions<TConnection>) {
  return {
    async ensure(scope: DesktopConnectionScopeInput): Promise<ConnectionEnsureEnvelope<TConnection>> {
      try {
        return { ok: true, connection: serializeConnection(await coordinator.ensure(normalizeScope(scope))) }
      } catch (error) {
        if (error instanceof ConnectionLifecycleError) {
          return { ok: false, error: error.data }
        }

        throw error
      }
    },
    inspect(scope: DesktopConnectionScopeInput): DesktopConnectionSnapshot<TConnection> {
      return coordinator.inspect(normalizeScope(scope))
    }
  }
}

export function sanitizeConnectionLifecycleSnapshot<TConnection>(
  snapshot: DesktopConnectionSnapshot<TConnection>
): PublicConnectionLifecycleSnapshot {
  const { connection: _connection, ...publicSnapshot } = snapshot

  return publicSnapshot
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype
}

function exactKeys(value: Record<string, unknown>, required: readonly string[], optional: readonly string[] = []) {
  const keys = Object.keys(value)
  const allowed = new Set([...required, ...optional])

  return required.every(key => Object.hasOwn(value, key)) && keys.every(key => allowed.has(key))
}

function decodeScope(value: unknown): DesktopConnectionScope | null {
  if (!isPlainRecord(value) || !exactKeys(value, ['connectionId', 'profile'])) {
    return null
  }

  if (
    !(value.connectionId === null || (typeof value.connectionId === 'string' && value.connectionId.length <= 256)) ||
    typeof value.profile !== 'string' ||
    !value.profile ||
    value.profile.length > 256 ||
    value.profile.includes('\0')
  ) {
    return null
  }

  return { connectionId: value.connectionId as null | string, profile: value.profile as string }
}

function decodeError(value: unknown): DesktopConnectionErrorData | null {
  if (
    !isPlainRecord(value) ||
    !exactKeys(value, ['attemptId', 'code', 'elapsedMs', 'message', 'phase', 'retryable', 'scope'])
  ) {
    return null
  }

  const scope = decodeScope(value.scope)
  const phases: DesktopConnectionPhase[] = ['resolve', 'launch', 'port', 'health', 'remote']

  const codes = [
    'launch_failed',
    'port_timeout',
    'health_timeout',
    'remote_unreachable',
    'auth_required',
    'missing_connection',
    'missing_profile',
    'invalidated',
    'attempt_timeout',
    'ipc_timeout'
  ]

  if (
    !scope ||
    !(value.attemptId === null || (Number.isSafeInteger(value.attemptId) && Number(value.attemptId) >= 0)) ||
    !Number.isFinite(value.elapsedMs) ||
    Number(value.elapsedMs) < 0 ||
    typeof value.code !== 'string' ||
    !codes.includes(value.code) ||
    typeof value.message !== 'string' ||
    value.message.length > 2_048 ||
    typeof value.phase !== 'string' ||
    !phases.includes(value.phase as DesktopConnectionPhase) ||
    typeof value.retryable !== 'boolean'
  ) {
    return null
  }

  return {
    attemptId: value.attemptId as number | null,
    code: value.code as DesktopConnectionErrorData['code'],
    elapsedMs: Number(value.elapsedMs),
    message: value.message,
    phase: value.phase as DesktopConnectionPhase,
    retryable: value.retryable,
    scope
  }
}

export function decodeConnectionLifecycleSnapshot(value: unknown): PublicConnectionLifecycleSnapshot | null {
  if (
    !isPlainRecord(value) ||
    !exactKeys(value, ['attemptId', 'elapsedMs', 'phase', 'scope', 'state'], ['error'])
  ) {
    return null
  }

  const scope = decodeScope(value.scope)
  const states: DesktopConnectionState[] = ['absent', 'starting', 'ready', 'failed']
  const phases: DesktopConnectionPhase[] = ['resolve', 'launch', 'port', 'health', 'remote']
  const phase = value.phase
  const error = value.error === undefined ? undefined : decodeError(value.error)

  if (
    !scope ||
    !(value.attemptId === null || (Number.isSafeInteger(value.attemptId) && Number(value.attemptId) >= 0)) ||
    !Number.isFinite(value.elapsedMs) ||
    Number(value.elapsedMs) < 0 ||
    !(phase === null || (typeof phase === 'string' && phases.includes(phase as DesktopConnectionPhase))) ||
    typeof value.state !== 'string' ||
    !states.includes(value.state as DesktopConnectionState) ||
    (value.error !== undefined && !error)
  ) {
    return null
  }

  return {
    attemptId: value.attemptId as number | null,
    elapsedMs: Number(value.elapsedMs),
    ...(error ? { error } : {}),
    phase: phase as DesktopConnectionPhase | null,
    scope,
    state: value.state as DesktopConnectionState
  }
}

export function subscribeConnectionLifecycle(
  ipc: {
    on: (channel: string, listener: (event: unknown, payload: unknown) => void) => unknown
    removeListener: (channel: string, listener: (event: unknown, payload: unknown) => void) => unknown
  },
  callback: (snapshot: PublicConnectionLifecycleSnapshot) => void
) {
  const listener = (_event: unknown, payload: unknown) => {
    const snapshot = decodeConnectionLifecycleSnapshot(payload)

    if (snapshot) {
      callback(snapshot)
    }
  }

  ipc.on(CONNECTION_LIFECYCLE_CHANNEL, listener)

  return () => {
    ipc.removeListener(CONNECTION_LIFECYCLE_CHANNEL, listener)
  }
}

export async function invokeWithConnectionWatchdog<TConnection>(
  invoke: () => Promise<ConnectionEnsureEnvelope<TConnection>>,
  scope: DesktopConnectionScopeInput,
  watchdogMs: number
): Promise<TConnection> {
  const envelope = await invokeConnectionIpcWithWatchdog(invoke, scope, watchdogMs)

  if (envelope.ok === false) {
    throw new ConnectionLifecycleError(envelope.error)
  }

  return envelope.connection
}

export async function invokeConnectionIpcWithWatchdog<TResult>(
  invoke: () => Promise<TResult>,
  scope: DesktopConnectionScopeInput,
  watchdogMs: number
): Promise<TResult> {
  const normalizedScope: DesktopConnectionScope = {
    connectionId: String(scope.connectionId ?? '').trim() || null,
    profile: String(scope.profile ?? '').trim() || 'default'
  }

  let timer: ReturnType<typeof setTimeout> | undefined

  const timeout = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => {
      reject(
        new ConnectionLifecycleError({
          attemptId: null,
          code: 'ipc_timeout',
          elapsedMs: watchdogMs,
          message: 'Hermes connection IPC did not settle after the Electron lifecycle deadline.',
          phase: 'resolve',
          retryable: true,
          scope: normalizedScope
        })
      )
    }, watchdogMs)
  })

  try {
    return await Promise.race([Promise.resolve().then(invoke), timeout])
  } finally {
    if (timer !== undefined) {
      clearTimeout(timer)
    }
  }
}
