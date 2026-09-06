import type { QueryClient, QueryKey } from '@tanstack/react-query'
import { atom } from 'nanostores'

import {
  createLifecycleRequestId,
  discardLifecycleClockObservation,
  LifecycleApiError,
  type LifecycleClockObservation,
  type LifecycleClockSample,
  type LifecycleConnectionBinding,
  type LifecycleStart,
  observeLifecycleClock
} from '@/api/workflow-marketplace-lifecycle'
import { createMarketplaceBindingCoordinator } from '@/lib/workflow-marketplace-connection-binding'
import {
  decodeLifecycleAdmission,
  decodeLifecycleEvicted,
  decodeLifecycleOperationPage,
  decodeLifecyclePackageState,
  decodeLifecycleSelection,
  decodeLifecycleStartBody,
  decodeLifecycleSubject,
  sameLifecycleIdentity,
  sameLifecycleValue
} from '@/lib/workflow-marketplace-lifecycle-codec'
import { createMarketplaceReconciliation } from '@/lib/workflow-marketplace-reconciliation'
import {
  acceptSupervisedOperation,
  createSupervisionScheduler,
  supervisionKey,
  waitForSupervision
} from '@/lib/workflow-marketplace-supervision'
import {
  type _CheckBody,
  type _ConfirmBody,
  type _IdentityBody,
  type _InstallBody,
  type _TrustBody,
  type LifecycleOperation,
  lifecycleRules,
  type PackageIdentity,
  type PackageState
} from '@/types/workflow-marketplace-lifecycle'

export interface MarketplaceScope {
  connectionId: string | null
  profile: string
}
type IntentBody<K extends LifecycleOperation['kind']> = K extends `${string}_confirm`
  ? _ConfirmBody
  : K extends `trust_${string}`
    ? _TrustBody
    : K extends 'install_prepare'
      ? _InstallBody
      : K extends 'update_check'
        ? _CheckBody
        : K extends 'refresh' | 'inspect'
          ? Record<string, never>
          : _IdentityBody

export type MarketplaceIntent = {
  [K in LifecycleOperation['kind']]: Pick<
    Extract<LifecycleOperation, { kind: K }>,
    'kind' | 'subject' | 'selection'
  > & {
    body: IntentBody<K>
  }
}[LifecycleOperation['kind']]
export interface SupervisedRecord {
  readonly key: string
  readonly binding: LifecycleConnectionBinding
  readonly requestId: string
  readonly operationId: string | null
  readonly kind: LifecycleOperation['kind']
  readonly subject: LifecycleOperation['subject']
  readonly selection: LifecycleOperation['selection']
  readonly status:
    'admitting' | 'admission_unknown' | 'watching' | 'status_unknown' | 'suspended' | 'terminal' | 'evicted'
  readonly callPending: boolean
  readonly barrier: boolean
  readonly operation: LifecycleOperation | null
  readonly errorCode: LifecycleApiError['code'] | null
  readonly admissionWindowClosed: boolean
}
export interface PackageGate {
  state: 'ready' | 'busy' | 'reconciling' | 'unknown' | 'recovery_required'
  packageState: PackageState | null
}
export interface SupervisionApi {
  capabilities(scope: { connectionId: string | null; profile: string; connectionGeneration: number }): Promise<unknown>
  start(
    input: LifecycleStart,
    binding: LifecycleConnectionBinding,
    observation: LifecycleClockObservation,
    now: LifecycleClockSample
  ): Promise<unknown>
  get(id: string, binding: LifecycleConnectionBinding): Promise<unknown>
  lookup(requestId: string, binding: LifecycleConnectionBinding): Promise<unknown>
  list(binding: LifecycleConnectionBinding, options?: { cursor?: string }): Promise<unknown>
  cancel(id: string, binding: LifecycleConnectionBinding): Promise<unknown>
  packageState(identity: PackageIdentity, binding: LifecycleConnectionBinding): Promise<unknown>
}
export interface SupervisorOptions {
  api: SupervisionApi
  queryClient: QueryClient
  clock: () => LifecycleClockSample
  visibility: { get(): boolean; listen(listener: () => void): () => void }
  connections: {
    resolveConnection(scope: MarketplaceScope): Promise<{ connectionId?: string | null; connectionGeneration?: number }>
    isConnected(scope: MarketplaceScope): boolean
    subscribe(listener: (scope: MarketplaceScope | null, reason: string) => void): () => void
  }
}
interface Entry {
  record: SupervisedRecord
  call?: AbortController
  cadence?: AbortController
  replay?: () => Promise<unknown>
  replayExpiry?: AbortController
}
const scopeKey = (scope: MarketplaceScope) => JSON.stringify([scope.connectionId, scope.profile])

const sameAuthority = (a: LifecycleConnectionBinding, b: LifecycleConnectionBinding) =>
  a.connectionId === b.connectionId &&
  a.profile === b.profile &&
  a.registryEpoch === b.registryEpoch &&
  a.principalBinding === b.principalBinding

const isMutation = (kind: LifecycleOperation['kind']) => kind.endsWith('_confirm') || kind === 'trust_revoke'
const invalid = () => new LifecycleApiError('marketplace_invalid_response', 0)

const safeError = (error: unknown) =>
  error instanceof LifecycleApiError
    ? new LifecycleApiError(error.code, error.status)
    : new LifecycleApiError('marketplace_network_error', 0)

const requiresProbe = (error: LifecycleApiError) =>
  error.status === 401 ||
  error.status === 403 ||
  [
    'marketplace_principal_changed',
    'marketplace_epoch_changed',
    'marketplace_operation_not_found',
    'marketplace_admission_not_found',
    'marketplace_connection_generation_changed'
  ].includes(error.code)

function freeze<T>(value: T): T {
  if (value && typeof value === 'object') {
    for (const child of Object.values(value)) {
      freeze(child)
    }

    Object.freeze(value)
  }

  return value
}

/** One main-window owner. Views subscribe; detaching a view never disposes work. */
export function createMarketplaceSupervisor(options: SupervisorOptions) {
  const { api, connections, visibility, clock } = options
  const records = atom<readonly SupervisedRecord[]>([])
  const status = atom<'available' | 'restart_required' | 'disposed'>('available')
  const entries = new Set<Entry>()
  const scheduler = createSupervisionScheduler()
  const observations = new Map<string, LifecycleClockObservation>()
  const samples = new Map<string, { capabilities: unknown; received: LifecycleClockSample }>()
  const scans = new Map<string, Promise<LifecycleConnectionBinding | null>>()
  const scanControllers = new Map<string, AbortController>()
  const packageCalls = new Map<AbortController, LifecycleConnectionBinding>()
  const knownScopes = new Map<string, MarketplaceScope>()
  const scanned = new Set<string>()
  const reconciliation = createMarketplaceReconciliation(options.queryClient, binding => bindings.isCurrent(binding))
  let disposed = false

  function publish() {
    const resolved = [...entries].filter(entry => entry.record.status === 'terminal' && !entry.record.barrier)

    for (const entry of resolved.slice(0, Math.max(0, resolved.length - 128))) {
      entries.delete(entry)
    }

    records.set(Object.freeze([...entries].map(entry => entry.record)))
    reconciliation.changed()
  }

  function update(entry: Entry, changes: Partial<SupervisedRecord>) {
    const next = { ...entry.record, ...changes }
    next.key = supervisionKey(next.binding, next.requestId, next.operationId)
    entry.record = freeze(next)
    publish()
  }

  function forgetReplay(entry: Entry) {
    entry.replay = undefined
    entry.replayExpiry?.abort()
    entry.replayExpiry = undefined
  }

  function stop(entry: Entry) {
    entry.cadence?.abort()
    entry.cadence = undefined
    entry.call?.abort()
    entry.call = undefined
    forgetReplay(entry)

    const nextStatus = entry.record.status === 'terminal' ? 'terminal' : 'suspended'

    if (entry.record.status !== nextStatus || entry.record.callPending) {
      update(entry, { status: nextStatus, callPending: false })
    }
  }

  function clearScopeWork(scope: MarketplaceScope | null) {
    for (const [key, controller] of scanControllers) {
      if (scope === null || key === scopeKey(scope)) {
        controller.abort()
        scanControllers.delete(key)
      }
    }

    for (const [controller, binding] of packageCalls) {
      if (scope === null || scopeKey(scope) === scopeKey(binding)) {
        controller.abort()
        packageCalls.delete(controller)
      }
    }

    for (const entry of entries) {
      if (scope === null || scopeKey(scope) === scopeKey(entry.record.binding)) {
        stop(entry)
      }
    }

    for (const [key, observation] of observations) {
      if (scope === null || key === scopeKey(scope)) {
        discardLifecycleClockObservation(observation)
        observations.delete(key)
      }
    }

    if (scope === null) {
      scans.clear()
      samples.clear()
      scanned.clear()
      knownScopes.clear()
    } else {
      scans.delete(scopeKey(scope))
      samples.delete(scopeKey(scope))
      scanned.delete(scopeKey(scope))
    }
  }

  function ownedScopes() {
    // Every registered scan enters knownScopes before awaiting any descriptor/query work.
    const scopes = new Map(knownScopes)

    const include = (scope: MarketplaceScope) =>
      scopes.set(scopeKey(scope), {
        connectionId: scope.connectionId,
        profile: scope.profile
      })

    for (const binding of packageCalls.values()) {
      include(binding)
    }

    for (const entry of entries) {
      if (entry.call || entry.cadence || entry.replay) {
        include(entry.record.binding)
      }
    }

    return [...scopes.values()]
  }

  function assertScopeOwner(
    scope: MarketplaceScope,
    controller: AbortController | undefined
  ): asserts controller is AbortController {
    if (
      !controller ||
      controller.signal.aborted ||
      scanControllers.get(scopeKey(scope)) !== controller ||
      disposed ||
      status.get() === 'restart_required' ||
      !visibility.get() ||
      !connections.isConnected(scope)
    ) {
      throw new DOMException('Observation stopped.', 'AbortError')
    }
  }

  const bindings = createMarketplaceBindingCoordinator({
    queries: options.queryClient,
    resolveConnection: async scope => {
      const controller = scanControllers.get(scopeKey(scope))
      assertScopeOwner(scope, controller)
      const descriptor = await connections.resolveConnection(scope)
      assertScopeOwner(scope, controller)

      return descriptor
    },
    getCapabilities: async scope => {
      const controller = scanControllers.get(scopeKey(scope))
      assertScopeOwner(scope, controller)

      const capabilities = await scheduler.run(() => {
        assertScopeOwner(scope, controller)

        return api.capabilities(scope)
      }, controller.signal)

      assertScopeOwner(scope, controller)
      samples.set(scopeKey(scope), { capabilities, received: clock() })

      return capabilities
    },
    changed: scope => {
      reconciliation.changed()

      if (scope === null) {
        if (status.get() === 'restart_required' || disposed) {
          return
        }

        status.set('restart_required')
        clearScopeWork(null)

        return
      }

      // The coordinator emits per-scope invalidations before its one global exhaustion notification.
      if (bindings.state(scope).kind === 'restart_required') {
        return
      }

      for (const [controller, binding] of packageCalls) {
        if ((scope === null || scopeKey(scope) === scopeKey(binding)) && !bindings.isCurrent(binding)) {
          controller.abort()
          packageCalls.delete(controller)
        }
      }

      for (const entry of entries) {
        if (
          (scope === null || scopeKey(scope) === scopeKey(entry.record.binding)) &&
          !bindings.isCurrent(entry.record.binding)
        ) {
          stop(entry)
        }
      }
    }
  })

  function usable(binding: LifecycleConnectionBinding) {
    return (
      !disposed &&
      status.get() !== 'restart_required' &&
      visibility.get() &&
      connections.isConnected(binding) &&
      bindings.isCurrent(binding)
    )
  }

  function suspend(scope: MarketplaceScope | null, reason: string) {
    if (disposed) {
      return
    }

    if (scope === null) {
      bindings.notify({ kind: 'exhausted' })
    } else {
      if (reason === 'disconnect') {
        knownScopes.delete(scopeKey(scope))
      }

      clearScopeWork(scope)
      bindings.suspend(scope, reason)
    }
  }

  function accept(entry: Entry, value: unknown, binding: LifecycleConnectionBinding) {
    if (!usable(binding)) {
      return
    }

    const evicted = decodeLifecycleEvicted(value)

    if (evicted) {
      const expected = entry.record

      if (
        evicted.profile !== binding.profile ||
        evicted.registry_epoch !== binding.registryEpoch ||
        evicted.request_id !== expected.requestId ||
        (expected.operationId && evicted.operation_id !== expected.operationId) ||
        evicted.kind !== expected.kind ||
        !sameLifecycleValue(evicted.subject, expected.subject) ||
        !sameLifecycleValue(evicted.selection, expected.selection)
      ) {
        throw invalid()
      }

      forgetReplay(entry)
      update(entry, { binding, operationId: evicted.operation_id, status: 'evicted', errorCode: null })

      return
    }

    const operation = acceptSupervisedOperation(value, binding, entry.record)

    if (entry.record.operation && ['succeeded', 'failed', 'cancelled'].includes(entry.record.operation.state)) {
      if (!sameLifecycleValue(operation, entry.record.operation)) {
        throw invalid()
      }

      update(entry, { binding, status: 'terminal', errorCode: null })

      return
    }

    forgetReplay(entry)
    const terminal = operation.state !== 'pending' && operation.state !== 'running'

    if (terminal && isMutation(operation.kind) && operation.subject.type === 'package') {
      reconciliation.advance(binding, operation.subject.identity)
    }

    update(entry, {
      binding,
      operationId: operation.id,
      operation,
      status: terminal ? 'terminal' : 'watching',
      errorCode: null,
      barrier: isMutation(operation.kind) || !terminal
    })
  }

  function schedule(entry: Entry) {
    if (entry.cadence || entry.call || entry.record.status !== 'watching' || !usable(entry.record.binding)) {
      return
    }

    const cadence = new AbortController()
    entry.cadence = cadence
    void waitForSupervision(500, cadence.signal)
      .then(() => {
        if (entry.cadence !== cadence) {
          return
        }

        entry.cadence = undefined

        return run(entry, 'get')
      })
      .catch(() => undefined)
  }

  async function run(
    entry: Entry,
    action: 'post' | 'get' | 'lookup' | 'cancel',
    target = entry.record.binding,
    reprobe = true
  ) {
    if (entry.call || !usable(target)) {
      return
    }

    entry.cadence?.abort()
    entry.cadence = undefined
    const controller = new AbortController()
    entry.call = controller
    update(entry, { callPending: true })
    let probe = false

    try {
      const call = () => {
        const record = entry.record

        if (action === 'post') {
          if (!entry.replay) {
            throw invalid()
          }

          return entry.replay()
        }

        if (action === 'lookup') {
          return api.lookup(record.requestId, target)
        }

        if (!record.operationId) {
          throw invalid()
        }

        return action === 'cancel' ? api.cancel(record.operationId, target) : api.get(record.operationId, target)
      }

      const value = await scheduler.run(call, controller.signal)

      if (controller.signal.aborted || entry.call !== controller || !usable(target)) {
        return
      }

      if (action === 'lookup') {
        const admission = decodeLifecycleAdmission(value)

        if (!admission) {
          throw invalid()
        }

        accept(entry, admission.state === 'found' ? admission.operation : admission, target)
      } else {
        accept(entry, value, target)
      }
    } catch (error) {
      if (controller.signal.aborted || entry.call !== controller || disposed) {
        return
      }

      const failure = safeError(error)

      if (failure.code === 'marketplace_connection_generation_exhausted') {
        suspend(null, failure.code)

        return
      }

      if (!bindings.isCurrent(target)) {
        return
      }

      if (
        !reprobe &&
        (failure.code === 'marketplace_admission_not_found' || failure.code === 'marketplace_request_expired')
      ) {
        const observation = observations.get(scopeKey(target))
        const issued = Number(entry.record.requestId.split('_')[2])
        const elapsed = observation ? clock().monotonicNowMs - observation.monotonicReceivedMs : NaN

        const closed =
          observation && elapsed >= 0 && elapsed <= 300000 && observation.serverTimeMs + elapsed > issued + 300000

        const state =
          closed && entry.record.subject.type === 'package'
            ? await reconcilePackage(target, entry.record.subject.identity, false)
            : null

        if (usable(target) && entry.call === controller) {
          update(entry, {
            status: entry.record.operationId ? 'status_unknown' : 'admission_unknown',
            admissionWindowClosed: Boolean(
              state && !state.busy && state.recovery === 'clear' && state.state !== 'unconfirmed'
            ),
            errorCode: failure.code
          })
        }
      } else if (requiresProbe(failure)) {
        suspend(target, failure.code)
        probe = reprobe
      } else {
        update(entry, {
          status: entry.record.operationId ? 'status_unknown' : 'admission_unknown',
          errorCode: failure.code
        })
      }
    } finally {
      if (entry.call === controller) {
        entry.call = undefined
        update(entry, { callPending: false })
      }
    }

    if (probe) {
      await reconcileScope(target, false)
    }

    schedule(entry)
  }

  function add(
    binding: LifecycleConnectionBinding,
    expected: Omit<
      SupervisedRecord,
      'key' | 'binding' | 'callPending' | 'barrier' | 'operation' | 'errorCode' | 'admissionWindowClosed'
    >
  ): Entry {
    const entry = {
      record: freeze({
        ...expected,
        binding: { ...binding },
        key: supervisionKey(binding, expected.requestId, expected.operationId),
        callPending: false,
        barrier: true,
        operation: null,
        errorCode: null,
        admissionWindowClosed: false
      })
    }

    entries.add(entry)

    if (isMutation(expected.kind) && expected.subject.type === 'package') {
      reconciliation.advance(binding, expected.subject.identity)
    }

    publish()

    return entry
  }

  function find(key: string) {
    return [...entries].find(entry => entry.record.key === key)
  }

  function snapshotOwner(operation: LifecycleOperation, binding: LifecycleConnectionBinding) {
    const owners = [...entries].filter(
      entry =>
        sameAuthority(entry.record.binding, binding) &&
        (entry.record.requestId === operation.request_id || entry.record.operationId === operation.id)
    )

    if (owners.length > 1) {
      throw invalid()
    }

    const owner = owners[0]

    if (owner) {
      acceptSupervisedOperation(operation, binding, owner.record)
    }

    return owner
  }

  async function scan(binding: LifecycleConnectionBinding, signal: AbortSignal) {
    for (let attempt = 0; attempt < 2; attempt++) {
      const items: LifecycleOperation[] = [],
        ids = new Set<string>(),
        requests = new Set<string>(),
        cursors = new Set<string>()

      let cursor: string | undefined

      try {
        do {
          const page = decodeLifecycleOperationPage(
            await scheduler.run(() => api.list(binding, cursor ? { cursor } : {}), signal)
          )

          if (!page || !usable(binding)) {
            throw invalid()
          }

          for (const operation of page.items) {
            if (
              operation.profile !== binding.profile ||
              operation.registry_epoch !== binding.registryEpoch ||
              ids.has(operation.id) ||
              requests.has(operation.request_id)
            ) {
              throw invalid()
            }

            ids.add(operation.id)
            requests.add(operation.request_id)
            items.push(operation)
          }

          if (items.length > 1088 || (page.next_cursor && cursors.has(page.next_cursor))) {
            throw invalid()
          }

          cursor = page.next_cursor ?? undefined

          if (cursor) {
            cursors.add(cursor)
          }
        } while (cursor)

        return items
      } catch (error) {
        if (attempt === 0 && error instanceof LifecycleApiError && error.code === 'marketplace_list_expired') {
          continue
        }

        throw error
      }
    }

    throw invalid()
  }

  function reconcileScope(scope: MarketplaceScope, reprobe = true): Promise<LifecycleConnectionBinding | null> {
    if (disposed || status.get() === 'restart_required') {
      return Promise.resolve(null)
    }

    knownScopes.set(scopeKey(scope), { connectionId: scope.connectionId, profile: scope.profile })

    if (!visibility.get() || !connections.isConnected(scope)) {
      return Promise.resolve(null)
    }

    const key = scopeKey(scope),
      running = scans.get(key)

    if (running) {
      return running
    }

    const controller = new AbortController()
    scanControllers.set(key, controller)

    const pending = (async () => {
      scanned.delete(key)
      const binding = await bindings.probe(scope)

      if (!binding || disposed || !usable(binding)) {
        return null
      }

      const sample = samples.get(key)

      if (!sample) {
        return null
      }

      const previous = observations.get(key)

      if (previous) {
        discardLifecycleClockObservation(previous)
      }

      observations.set(key, observeLifecycleClock(sample.capabilities, binding, sample.received))
      reconciliation.bind(binding)
      samples.delete(key)

      try {
        for (const entry of [...entries]) {
          if (
            sameAuthority(entry.record.binding, binding) &&
            (entry.record.status !== 'terminal' || !sameLifecycleValue(entry.record.binding, binding))
          ) {
            await run(entry, 'lookup', binding, false)
          }
        }

        if (!usable(binding)) {
          return null
        }

        const items = await scan(binding, controller.signal)
        // Validate both directions for the whole snapshot before publishing any new observation.
        const snapshot = items.map(operation => ({ operation, known: snapshotOwner(operation, binding) }))

        for (const { operation, known } of snapshot) {
          if (!known) {
            // Known requests use exact lookup facts; a retained snapshot may be older.
            const entry = add(binding, {
              requestId: operation.request_id,
              operationId: operation.id,
              kind: operation.kind,
              subject: operation.subject,
              selection: operation.selection,
              status: 'watching'
            })

            accept(entry, operation, binding)
          }
        }

        scanned.add(key)
        reconciliation.changed()

        for (const entry of entries) {
          schedule(entry)
        }

        return binding
      } catch (error) {
        const failure = safeError(error)

        if (controller.signal.aborted) {
          return null
        }

        if (failure.code === 'marketplace_connection_generation_exhausted') {
          suspend(null, failure.code)
        } else if (bindings.isCurrent(binding)) {
          suspend(scope, failure.code)

          if (reprobe && requiresProbe(failure)) {
            // suspend removed this scan's ownership: the replacement cannot await this promise.
            return reconcileScope(scope, false)
          }
        }

        return null
      }
    })().finally(() => {
      if (scans.get(key) === pending) {
        scans.delete(key)
      }

      if (scanControllers.get(key) === controller) {
        scanControllers.delete(key)
      }
    })

    scans.set(key, pending)

    return pending
  }

  function start(input: MarketplaceIntent, binding: LifecycleConnectionBinding): Promise<string> {
    if (!usable(binding) || !scanned.has(scopeKey(binding))) {
      return Promise.reject(new LifecycleApiError('marketplace_clock_revalidation_required', 0))
    }

    if ([...entries].filter(entry => entry.record.barrier).length >= 128) {
      return Promise.reject(new LifecycleApiError('marketplace_admission_capacity', 503))
    }

    const subject = decodeLifecycleSubject(input.subject),
      selection = input.selection === null ? null : decodeLifecycleSelection(input.selection)

    const kind = input.kind,
      body = decodeLifecycleStartBody(kind, input.body)

    if (
      !subject ||
      !Object.hasOwn(lifecycleRules.subjects, kind) ||
      !(lifecycleRules.subjects[kind] as readonly string[]).includes(subject.type) ||
      (input.selection !== null && !selection) ||
      kind.startsWith('trust_') !== (selection !== null) ||
      !body
    ) {
      return Promise.reject(invalid())
    }

    if (
      [...entries].some(
        entry =>
          sameAuthority(entry.record.binding, binding) &&
          entry.record.barrier &&
          sameLifecycleValue(entry.record.subject, subject)
      )
    ) {
      return Promise.reject(new LifecycleApiError('marketplace_request_conflict', 409))
    }

    const observation = observations.get(scopeKey(binding))

    if (!observation) {
      return Promise.reject(new LifecycleApiError('marketplace_clock_revalidation_required', 0))
    }

    let requestId: string

    try {
      requestId = createLifecycleRequestId(observation, clock(), binding)
    } catch (error) {
      const failure = safeError(error)
      suspend(binding, failure.code)
      void reconcileScope(binding)

      return Promise.reject(failure)
    }

    const entry = add(binding, { requestId, operationId: null, kind, subject, selection, status: 'admitting' })
    const captured = freeze({ requestId, kind, subject, selection, body })
    entry.replay = () => api.start(captured, binding, observation, clock())
    const expiry = new AbortController()
    entry.replayExpiry = expiry
    void waitForSupervision(15000, expiry.signal)
      .then(() => {
        forgetReplay(entry)

        if (entry.call && !entry.record.operationId) {
          entry.call.abort()
          entry.call = undefined
          update(entry, { status: 'admission_unknown', callPending: false, errorCode: 'marketplace_network_error' })
        }
      })
      .catch(() => undefined)

    return run(entry, 'post').then(() => entry.record.key)
  }

  async function retry(key: string) {
    const entry = find(key)

    if (!entry || disposed || status.get() === 'restart_required') {
      return
    }

    if (!usable(entry.record.binding)) {
      await reconcileScope(entry.record.binding)

      return
    }

    await run(entry, entry.record.operationId ? 'get' : entry.replay ? 'post' : 'lookup')
  }

  async function cancel(key: string) {
    const entry = find(key)

    if (!entry || entry.record.status === 'terminal') {
      return
    }

    await run(entry, entry.record.operationId ? 'cancel' : 'lookup')
  }

  async function reconcilePackage(
    binding: LifecycleConnectionBinding,
    identity: PackageIdentity,
    reprobe = true
  ): Promise<PackageState | null> {
    if (!usable(binding)) {
      return null
    }

    const controller = new AbortController()
    const generation = reconciliation.read(binding, identity).generation
    packageCalls.set(controller, binding)

    try {
      const value = decodeLifecyclePackageState(
        await scheduler.run(() => api.packageState(identity, binding), controller.signal)
      )

      if (
        !value ||
        !usable(binding) ||
        value.profile !== binding.profile ||
        !sameLifecycleIdentity(value.identity, identity)
      ) {
        return null
      }

      if (!reconciliation.accept(binding, identity, generation, value)) {
        return null
      }

      if (!value.busy && value.recovery === 'clear' && value.state !== 'unconfirmed') {
        for (const entry of entries) {
          const record = entry.record

          if (
            sameLifecycleValue(record.binding, binding) &&
            record.subject.type === 'package' &&
            sameLifecycleIdentity(record.subject.identity, identity) &&
            (record.status === 'terminal' || record.status === 'evicted' || record.admissionWindowClosed)
          ) {
            update(entry, { barrier: false })
          }
        }
      }

      return value
    } catch (error) {
      if (controller.signal.aborted) {
        return null
      }

      const failure = safeError(error)

      if (!disposed && failure.code === 'marketplace_connection_generation_exhausted') {
        suspend(null, failure.code)
      } else if (!disposed && bindings.isCurrent(binding) && requiresProbe(failure)) {
        suspend(binding, failure.code)

        if (reprobe) {
          await reconcileScope(binding, false)
        }
      }

      return null
    } finally {
      packageCalls.delete(controller)
    }
  }

  function getPackageGate(
    binding: LifecycleConnectionBinding,
    identity: PackageIdentity,
    projection?: QueryKey
  ): PackageGate {
    if (!usable(binding) || !scanned.has(scopeKey(binding))) {
      return { state: 'unknown', packageState: null }
    }

    const matching = [...entries]
      .map(entry => entry.record)
      .filter(
        record =>
          sameLifecycleValue(record.binding, binding) &&
          record.subject.type === 'package' &&
          sameLifecycleIdentity(record.subject.identity, identity)
      )

    const observation = reconciliation.read(binding, identity)
    const state = observation.packageState

    if (matching.some(record => record.status === 'admitting' || record.status === 'watching') || state?.busy) {
      return { state: 'busy', packageState: null }
    }

    if (matching.some(record => record.barrier && record.status !== 'terminal')) {
      return { state: 'unknown', packageState: null }
    }

    if (
      state?.recovery === 'required' ||
      (!state && matching.some(record => record.barrier && record.operation?.outcome?.type === 'recovery_required'))
    ) {
      return { state: 'recovery_required', packageState: null }
    }

    if (
      state?.state === 'unconfirmed' ||
      state?.recovery === 'unconfirmed' ||
      (!state && matching.some(record => record.barrier && record.operation?.outcome?.type === 'outcome_unknown'))
    ) {
      return { state: 'unknown', packageState: null }
    }

    if (state) {
      return {
        state:
          projection && !reconciliation.projectionFresh(binding, projection, observation.generation, identity)
            ? 'reconciling'
            : 'ready',
        packageState: state
      }
    }

    return { state: observation.generation ? 'reconciling' : 'unknown', packageState: null }
  }

  const offConnections = connections.subscribe(suspend)

  const offVisibility = visibility.listen(() => {
    if (!visibility.get()) {
      // Rotate the coordinator's private attempts too: it can still be awaiting final query cancellation.
      for (const scope of ownedScopes()) {
        suspend(scope, 'hidden')
      }
    } else {
      for (const scope of knownScopes.values()) {
        void reconcileScope(scope)
      }
    }
  })

  function dispose() {
    if (disposed) {
      return
    }

    disposed = true
    status.set('disposed')

    for (const scope of ownedScopes()) {
      bindings.suspend(scope, 'disposed')
    }

    clearScopeWork(null)
    reconciliation.dispose()
    offConnections()
    offVisibility()
  }

  return {
    $records: {
      get: records.get.bind(records),
      listen: records.listen.bind(records),
      subscribe: records.subscribe.bind(records)
    },
    $status: {
      get: status.get.bind(status),
      listen: status.listen.bind(status),
      subscribe: status.subscribe.bind(status)
    },
    bindings,
    reconciliation,
    start,
    retry,
    cancel,
    reconcileScope,
    reconcilePackage,
    getPackageGate,
    canUseCatalog(binding: LifecycleConnectionBinding, projection: QueryKey) {
      return (
        usable(binding) &&
        scanned.has(scopeKey(binding)) &&
        reconciliation.projectionFresh(binding, projection, reconciliation.scopeGeneration(binding)) &&
        ![...entries].some(
          ({ record }) => sameAuthority(record.binding, binding) && isMutation(record.kind) && record.barrier
        )
      )
    },
    dispose
  }
}
