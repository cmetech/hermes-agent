import type {
  DesktopConnectionLifecycleSnapshot,
  DesktopConnectionScopeInput,
  HermesConnection
} from '@/global'
import { RECONNECT_ATTEMPT_TIMEOUT_MS, withTimeout } from '@/lib/with-timeout'

interface RendererConnectionScope {
  connectionId: null | string
  profile: string
}

interface ConnectionEntry {
  attemptId: number | null
  connection?: HermesConnection
  promise?: Promise<HermesConnection>
  revision: number
}

const entries = new Map<string, ConnectionEntry>()
const latestAttemptIds = new Map<string, number>()
const revisions = new Map<string, number>()
let unsubscribeLifecycle: (() => void) | null = null
let subscribedBridge: Window['hermesDesktop'] | null = null

export function normalizeDesktopConnectionScope(scope: DesktopConnectionScopeInput): RendererConnectionScope {
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

function invalidateKey(key: string): void {
  const current = entries.get(key)
  const nextRevision = (revisions.get(key) ?? 0) + 1

  revisions.set(key, nextRevision)

  if (current) {
    current.revision = nextRevision
    entries.delete(key)
  }
}

function consumeLifecycle(snapshot: DesktopConnectionLifecycleSnapshot): void {
  const key = desktopConnectionScopeKey(snapshot.scope)
  const attemptId = snapshot.attemptId
  const latestAttemptId = latestAttemptIds.get(key)

  if (attemptId !== null && latestAttemptId !== undefined && attemptId < latestAttemptId) {
    return
  }

  if (attemptId !== null) {
    latestAttemptIds.set(key, attemptId)
  }

  const current = entries.get(key)

  if (snapshot.state === 'starting') {
    if (!current) {
      return
    }

    if (current.connection || (current.attemptId !== null && current.attemptId !== attemptId)) {
      invalidateKey(key)

      return
    }

    current.attemptId = attemptId

    return
  }

  if (snapshot.state === 'failed' || snapshot.state === 'absent') {
    invalidateKey(key)
  }
}

function subscribeToLifecycle(desktop: Window['hermesDesktop']): void {
  if (subscribedBridge === desktop) {
    return
  }

  unsubscribeLifecycle?.()

  if (subscribedBridge !== null) {
    entries.clear()
    latestAttemptIds.clear()
    revisions.clear()
  }

  unsubscribeLifecycle = desktop.onConnectionLifecycle?.(consumeLifecycle) ?? null
  subscribedBridge = desktop
}

function invokeEnsure(
  desktop: Window['hermesDesktop'],
  scope: DesktopConnectionScopeInput
): Promise<HermesConnection> {
  if (desktop.ensureConnection) {
    return desktop.ensureConnection(scope)
  }

  if (scope.connectionId && desktop.getConnectionFor) {
    return withTimeout(
      desktop.getConnectionFor(scope),
      RECONNECT_ATTEMPT_TIMEOUT_MS,
      'Timed out waiting for a legacy registry connection bridge'
    )
  }

  return withTimeout(
    desktop.getConnection(scope.profile),
    RECONNECT_ATTEMPT_TIMEOUT_MS,
    'Timed out waiting for a legacy desktop connection bridge'
  )
}

export function ensureDesktopConnection(scopeInput: DesktopConnectionScopeInput): Promise<HermesConnection> {
  const desktop = window.hermesDesktop

  if (!desktop) {
    return Promise.reject(new Error('Desktop IPC bridge is unavailable'))
  }

  subscribeToLifecycle(desktop)

  const scope = normalizeDesktopConnectionScope(scopeInput)

  const requestScope: DesktopConnectionScopeInput = {
    connectionId: scope.connectionId,
    profile:
      scope.connectionId === null && !String(scopeInput.profile ?? '').trim()
        ? null
        : scope.profile
  }

  const key = desktopConnectionScopeKey(scope)
  const current = entries.get(key)

  if (current?.connection) {
    return Promise.resolve(current.connection)
  }

  if (current?.promise) {
    return current.promise
  }

  const entry: ConnectionEntry = {
    attemptId: latestAttemptIds.get(key) ?? null,
    revision: revisions.get(key) ?? 0
  }

  let invoked: Promise<HermesConnection>

  try {
    invoked = invokeEnsure(desktop, requestScope)
  } catch (error) {
    invoked = Promise.reject(error)
  }

  const promise = Promise.resolve(invoked).then(
      connection => {
        if (entries.get(key) === entry && entry.revision === (revisions.get(key) ?? 0)) {
          if (desktop.ensureConnection) {
            entry.connection = connection
            entry.promise = undefined
          } else {
            entries.delete(key)
          }
        }

        return connection
      },
      error => {
        if (entries.get(key) === entry) {
          entries.delete(key)
        }

        throw error
      }
    )

  entry.promise = promise
  entries.set(key, entry)

  return promise
}

export function invalidateDesktopConnection(scopeInput: DesktopConnectionScopeInput): void {
  invalidateKey(desktopConnectionScopeKey(scopeInput))
}

export async function revalidateDesktopConnection(
  scopeInput: DesktopConnectionScopeInput = { connectionId: null, profile: null }
): Promise<{ ok: boolean; rebuilt: boolean }> {
  const desktop = window.hermesDesktop

  if (!desktop?.revalidateConnection) {
    return { ok: true, rebuilt: false }
  }

  const work = desktop.revalidateConnection()

  const result = desktop.ensureConnection
    ? await work
    : await withTimeout(work, RECONNECT_ATTEMPT_TIMEOUT_MS, 'Timed out revalidating a legacy desktop connection')

  if (result.rebuilt) {
    invalidateDesktopConnection(scopeInput)
  }

  return result
}

export function inspectDesktopConnection(
  scopeInput: DesktopConnectionScopeInput
): Promise<DesktopConnectionLifecycleSnapshot> {
  const desktop = window.hermesDesktop
  const scope = normalizeDesktopConnectionScope(scopeInput)

  if (desktop?.inspectConnection) {
    subscribeToLifecycle(desktop)

    return desktop.inspectConnection(scope)
  }

  return Promise.resolve({
    attemptId: null,
    elapsedMs: 0,
    phase: null,
    scope,
    state: 'absent'
  })
}

export function _resetDesktopConnectionForTests(): void {
  unsubscribeLifecycle?.()
  unsubscribeLifecycle = null
  subscribedBridge = null
  entries.clear()
  latestAttemptIds.clear()
  revisions.clear()
}
