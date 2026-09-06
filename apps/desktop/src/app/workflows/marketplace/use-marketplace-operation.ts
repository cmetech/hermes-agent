import { useCallback, useEffect, useMemo, useRef, useSyncExternalStore } from 'react'

import { profileScopeKey } from '@/api/client'
import type { LifecycleConnectionBinding } from '@/api/workflow-marketplace-lifecycle'
import type { WorkflowMarketplaceScope } from '@/hermes'
import { sameLifecycleValue } from '@/lib/workflow-marketplace-lifecycle-codec'
import type { createMarketplaceSupervisor, SupervisedRecord } from '@/store/workflow-marketplace-supervisor'

type MarketplaceSupervisor = ReturnType<typeof createMarketplaceSupervisor>
type OperationRecovery = 'evicted' | 'status'

const EMPTY_RECORDS: readonly SupervisedRecord[] = Object.freeze([])
const subscribeEmpty = () => () => undefined
const readEmpty = () => EMPTY_RECORDS

export interface MarketplaceDisplayedOperation {
  id: string
  phase: string
  progress: number
  state: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'
}

export interface MarketplaceOperationController {
  captureOrigin: () => MarketplaceOperationOrigin
  cancel: (operationId: string) => Promise<null | MarketplaceDisplayedOperation>
  errors: Readonly<Record<string, OperationRecovery>>
  isReconciling: boolean
  operationForSource: (sourceName: string) => MarketplaceDisplayedOperation | undefined
  operations: readonly MarketplaceDisplayedOperation[]
  originIsCurrent: (origin: MarketplaceOperationOrigin) => boolean
  reconcile: () => void
  retry: (operationId: string) => Promise<null | MarketplaceDisplayedOperation>
  start: (sourceName: string, origin?: MarketplaceOperationOrigin) => Promise<null | MarketplaceDisplayedOperation>
}

export interface MarketplaceOperationOrigin {
  readonly generation: number
  readonly scopeKey: string
}

/** Foreground adapter only. Exact admission, polling, replay and invalidation remain supervisor-owned. */
export function useMarketplaceOperation(
  scope: WorkflowMarketplaceScope,
  binding: LifecycleConnectionBinding | null,
  supervisor: MarketplaceSupervisor | null,
  enabled = true
): MarketplaceOperationController {
  const scopeKey = profileScopeKey(scope)

  const authorityKey = binding
    ? JSON.stringify([
        binding.connectionId,
        binding.connectionGeneration,
        binding.profile,
        binding.principalBinding,
        binding.registryEpoch
      ])
    : 'unbound'

  const generationRef = useRef(0)
  const renderedScopeRef = useRef(scopeKey)
  const renderedAuthorityRef = useRef(authorityKey)
  const intentGuardsRef = useRef(new Set<string>())
  const ownedKeysRef = useRef(new Map<string, string>())
  const mountedRef = useRef(true)
  const waitersRef = useRef(new Set<AbortController>())

  if (renderedScopeRef.current !== scopeKey || renderedAuthorityRef.current !== authorityKey) {
    renderedScopeRef.current = scopeKey
    renderedAuthorityRef.current = authorityKey
    generationRef.current += 1
    intentGuardsRef.current.clear()
    ownedKeysRef.current.clear()
  }

  const records = useSyncExternalStore(
    supervisor?.$records.subscribe ?? subscribeEmpty,
    supervisor?.$records.get ?? readEmpty,
    supervisor?.$records.get ?? readEmpty
  )

  // eslint-disable-next-line no-restricted-syntax -- tracks component lifetime for foreground waiter detachment
  useEffect(() => {
    mountedRef.current = true
    const intentGuards = intentGuardsRef.current
    const ownedKeys = ownedKeysRef.current
    const waiters = waitersRef.current

    return () => {
      mountedRef.current = false
      generationRef.current += 1
      intentGuards.clear()
      ownedKeys.clear()

      for (const waiter of waiters) {
        waiter.abort()
      }

      waiters.clear()
    }
  }, [authorityKey, scopeKey])

  const available = Boolean(
    enabled && binding && supervisor?.supports(binding, ['operations', 'admission_replay', 'sources'])
  )

  const matching = useMemo(
    () =>
      binding
        ? records.filter(
            record =>
              record.kind === 'refresh' &&
              record.subject.type === 'source' &&
              sameLifecycleValue(record.binding, binding)
          )
        : [],
    [binding, records]
  )

  const captureOrigin = useCallback(
    (): MarketplaceOperationOrigin => ({ generation: generationRef.current, scopeKey }),
    [scopeKey]
  )

  const originIsCurrent = useCallback(
    (origin: MarketplaceOperationOrigin) =>
      mountedRef.current && renderedScopeRef.current === origin.scopeKey && generationRef.current === origin.generation,
    []
  )

  const recordForSource = useCallback(
    (sourceName: string) => {
      const owned = ownedKeysRef.current.get(sourceName)
      const exact = owned ? matching.find(record => record.key === owned) : undefined

      if (exact) {
        return exact
      }

      const active = matching.filter(
        record =>
          record.subject.type === 'source' && record.subject.source_name === sourceName && record.status !== 'terminal'
      )

      // Public source subject is sufficient only for a single active record. Ambiguity fails closed.
      return active.length === 1 ? active[0] : undefined
    },
    [matching]
  )

  const operations = useMemo(() => matching.flatMap(record => (record.operation ? [record.operation] : [])), [matching])

  const errors = useMemo(() => {
    const next: Record<string, OperationRecovery> = {}

    const sources = new Set(
      matching.flatMap(record => (record.subject.type === 'source' ? [record.subject.source_name] : []))
    )

    for (const sourceName of sources) {
      const record = recordForSource(sourceName)

      if (!record) {
        continue
      }

      if (record.status === 'evicted') {
        next[sourceName] = 'evicted'
      } else if (
        record.status === 'status_unknown' ||
        record.status === 'admission_unknown' ||
        record.status === 'suspended'
      ) {
        next[sourceName] = 'status'
      }
    }

    return next
  }, [matching, recordForSource])

  const wait = useCallback(
    async (key: string) => {
      if (!supervisor) {
        return null
      }

      const waiter = new AbortController()
      waitersRef.current.add(waiter)

      try {
        const record = await supervisor.waitForRecord(key, waiter.signal)

        return record.operation
      } finally {
        waitersRef.current.delete(waiter)
      }
    },
    [supervisor]
  )

  const start = useCallback(
    async (sourceName: string, origin = captureOrigin()) => {
      const guard = `${origin.scopeKey}\0${sourceName}`

      if (!available || !binding || !supervisor || !originIsCurrent(origin) || intentGuardsRef.current.has(guard)) {
        return null
      }

      intentGuardsRef.current.add(guard)

      try {
        const key = await supervisor.start(
          { kind: 'refresh', subject: { type: 'source', source_name: sourceName }, selection: null, body: {} },
          binding
        )

        if (!originIsCurrent(origin)) {
          return null
        }

        ownedKeysRef.current.set(sourceName, key)

        return await wait(key)
      } catch {
        return null
      } finally {
        intentGuardsRef.current.delete(guard)
      }
    },
    [available, binding, captureOrigin, originIsCurrent, supervisor, wait]
  )

  const exactRecord = useCallback(
    (operationId: string) => {
      const candidates = matching.filter(record => record.operationId === operationId)

      return candidates.length === 1 ? candidates[0] : undefined
    },
    [matching]
  )

  const cancel = useCallback(
    async (operationId: string) => {
      const record = exactRecord(operationId)

      if (!available || !record || !supervisor || record.status === 'terminal') {
        return null
      }

      await supervisor.cancel(record.key)

      try {
        return await wait(record.key)
      } catch {
        return null
      }
    },
    [available, exactRecord, supervisor, wait]
  )

  const retry = useCallback(
    async (operationId: string) => {
      const record = exactRecord(operationId)

      if (!available || !record || !supervisor || record.status === 'terminal') {
        return null
      }

      await supervisor.retry(record.key)

      try {
        return await wait(record.key)
      } catch {
        return null
      }
    },
    [available, exactRecord, supervisor, wait]
  )

  const operationForSource = useCallback(
    (sourceName: string) => recordForSource(sourceName)?.operation ?? undefined,
    [recordForSource]
  )

  const reconcile = useCallback(() => {
    if (!enabled || !supervisor) {
      return
    }

    const normalized =
      typeof scope === 'object' && scope !== null
        ? { connectionId: scope.connectionId, profile: scope.profile ?? 'default' }
        : { connectionId: null, profile: scope ?? 'default' }

    void supervisor.reconcileScope(normalized)
  }, [enabled, scope, supervisor])

  return useMemo(
    () => ({
      cancel,
      captureOrigin,
      errors,
      isReconciling: enabled && !available,
      operationForSource,
      operations,
      originIsCurrent,
      reconcile,
      retry,
      start
    }),
    [
      available,
      cancel,
      captureOrigin,
      enabled,
      errors,
      operationForSource,
      operations,
      originIsCurrent,
      reconcile,
      retry,
      start
    ]
  )
}
