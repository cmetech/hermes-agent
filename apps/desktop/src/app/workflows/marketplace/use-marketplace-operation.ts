import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { profileScopeKey } from '@/api/client'
import {
  cancelWorkflowMarketplaceOperation,
  getWorkflowMarketplaceOperation,
  listWorkflowMarketplaceOperations
} from '@/hermes'
import type { WorkflowMarketplaceScope } from '@/hermes'
import type { WorkflowMarketplaceOperation } from '@/types/hermes'

import { marketplaceKeys } from './query-keys'

const POLL_MS = 500
const RECONCILE_LIMIT = 100
const RECONCILE_MAX = 200

type OperationRecovery = 'evicted' | 'status'

function terminal(operation: WorkflowMarketplaceOperation): boolean {
  return operation.state === 'succeeded' || operation.state === 'failed' || operation.state === 'cancelled'
}

function errorCode(error: unknown): null | string {
  return typeof error === 'object' && error !== null && 'code' in error && typeof error.code === 'string'
    ? error.code
    : null
}

function nextCadence(signal: AbortSignal): Promise<boolean> {
  return new Promise(resolve => {
    let timer: null | number = null

    const finish = (ready: boolean) => {
      document.removeEventListener('visibilitychange', visible)
      signal.removeEventListener('abort', abort)

      if (timer !== null) {
        window.clearTimeout(timer)
      }

      resolve(ready)
    }

    const schedule = () => {
      document.removeEventListener('visibilitychange', visible)
      timer = window.setTimeout(() => finish(true), POLL_MS)
    }

    const visible = () => {
      if (document.visibilityState === 'visible') {
        schedule()
      }
    }

    const abort = () => finish(false)

    if (signal.aborted) {
      finish(false)

      return
    }

    signal.addEventListener('abort', abort, { once: true })

    if (document.visibilityState === 'hidden') {
      document.addEventListener('visibilitychange', visible)
    } else {
      schedule()
    }
  })
}

export interface MarketplaceOperationController {
  captureOrigin: () => MarketplaceOperationOrigin
  cancel: (operationId: string) => Promise<null | WorkflowMarketplaceOperation>
  errors: Readonly<Record<string, OperationRecovery>>
  isReconciling: boolean
  operationForSource: (sourceName: string) => WorkflowMarketplaceOperation | undefined
  operations: readonly WorkflowMarketplaceOperation[]
  originIsCurrent: (origin: MarketplaceOperationOrigin) => boolean
  reconcile: () => void
  retry: (operationId: string) => Promise<null | WorkflowMarketplaceOperation>
  start: (
    sourceName: string,
    request: () => Promise<WorkflowMarketplaceOperation>,
    origin?: MarketplaceOperationOrigin
  ) => Promise<null | WorkflowMarketplaceOperation>
}

export interface MarketplaceOperationOrigin {
  readonly generation: number
  readonly scopeKey: string
}

export function useMarketplaceOperation(
  scope: WorkflowMarketplaceScope,
  enabled = true
): MarketplaceOperationController {
  const queryClient = useQueryClient()
  const scopeKey = profileScopeKey(scope)
  const explicitScope = typeof scope === 'object' && scope !== null
  const connectionId = explicitScope ? scope.connectionId : null
  const profile = explicitScope ? scope.profile : scope

  const requestScope = useMemo<WorkflowMarketplaceScope>(
    () => (explicitScope ? { connectionId, profile: profile ?? null } : profile),
    [connectionId, explicitScope, profile]
  )

  const generationRef = useRef(0)
  const renderedScopeKeyRef = useRef(scopeKey)
  const mountedRef = useRef(true)
  const intentGuardsRef = useRef(new Set<string>())
  const pollGuardsRef = useRef(new Set<string>())
  const cadenceControllersRef = useRef(new Map<string, AbortController>())
  const invalidatedRef = useRef(new Set<string>())
  const reconcileGuardRef = useRef<null | string>(null)
  const operationsRef = useRef(new Map<string, WorkflowMarketplaceOperation>())
  const [operations, setOperations] = useState<WorkflowMarketplaceOperation[]>([])
  const [errors, setErrors] = useState<Record<string, OperationRecovery>>({})
  const [isReconciling, setIsReconciling] = useState(enabled)
  const [reconcileNonce, setReconcileNonce] = useState(0)

  if (renderedScopeKeyRef.current !== scopeKey) {
    renderedScopeKeyRef.current = scopeKey
    generationRef.current += 1
  }

  const captureOrigin = useCallback(
    (): MarketplaceOperationOrigin => ({ generation: generationRef.current, scopeKey }),
    [scopeKey]
  )

  const originIsCurrent = useCallback(
    (origin: MarketplaceOperationOrigin) =>
      mountedRef.current &&
      renderedScopeKeyRef.current === origin.scopeKey &&
      generationRef.current === origin.generation,
    []
  )

  const abortCadences = useCallback(() => {
    for (const controller of cadenceControllersRef.current.values()) {
      controller.abort()
    }
    cadenceControllersRef.current.clear()
  }, [])

  const publish = useCallback((operation: WorkflowMarketplaceOperation, generation: number) => {
    if (
      !mountedRef.current ||
      generationRef.current !== generation ||
      operation.kind !== 'refresh' ||
      operation.source_name === null
    ) {
      return false
    }

    operationsRef.current.set(operation.id, operation)
    setOperations([...operationsRef.current.values()])
    setErrors(current => {
      if (!(operation.source_name in current)) {
        return current
      }

      const next = { ...current }
      delete next[operation.source_name]

      return next
    })

    return true
  }, [])

  const tombstone = useCallback((operation: WorkflowMarketplaceOperation, generation: number) => {
    if (!mountedRef.current || generationRef.current !== generation || operation.source_name === null) {
      return
    }

    operationsRef.current.delete(operation.id)
    setOperations([...operationsRef.current.values()])
    setErrors(existing => ({ ...existing, [operation.source_name]: 'evicted' }))
  }, [])

  const invalidateRefresh = useCallback(
    async (originScopeKey: string, operationId: string, generation: number) => {
      if (!mountedRef.current || generationRef.current !== generation || scopeKey !== originScopeKey) {
        return
      }

      const identity = `${originScopeKey}:${operationId}`

      if (invalidatedRef.current.has(identity)) {
        return
      }

      invalidatedRef.current.add(identity)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: marketplaceKeys.sources(originScopeKey) }),
        queryClient.invalidateQueries({ queryKey: marketplaceKeys.searchRoot(originScopeKey) })
      ])
    },
    [queryClient, scopeKey]
  )

  const poll = useCallback(
    async (
      initial: WorkflowMarketplaceOperation,
      generation: number,
      originScope: WorkflowMarketplaceScope,
      originScopeKey: string
    ): Promise<null | WorkflowMarketplaceOperation> => {
      const pollIdentity = `${originScopeKey}:${initial.id}`

      if (pollGuardsRef.current.has(pollIdentity)) {
        return null
      }

      pollGuardsRef.current.add(pollIdentity)
      const cadence = new AbortController()
      cadenceControllersRef.current.set(pollIdentity, cadence)

      let current = initial

      try {
        if (!publish(current, generation)) {
          return null
        }

        if (terminal(current)) {
          await invalidateRefresh(originScopeKey, current.id, generation)

          return current
        }

        while (mountedRef.current && generationRef.current === generation) {
          if (!(await nextCadence(cadence.signal))) {
            return null
          }

          if (!mountedRef.current || generationRef.current !== generation) {
            return null
          }

          try {
            current = await getWorkflowMarketplaceOperation(current.id, originScope)
          } catch (error) {
            if (!mountedRef.current || generationRef.current !== generation) {
              return null
            }

            const sourceName = current.kind === 'refresh' ? current.source_name : null

            if (errorCode(error) === 'marketplace_operation_not_found') {
              tombstone(current, generation)
              await invalidateRefresh(originScopeKey, current.id, generation)
            } else if (sourceName !== null) {
              setErrors(existing => ({
                ...existing,
                [sourceName]: 'status'
              }))
            }

            return null
          }

          if (!publish(current, generation)) {
            return null
          }

          if (terminal(current)) {
            await invalidateRefresh(originScopeKey, current.id, generation)

            return current
          }
        }

        return null
      } finally {
        cadence.abort()
        cadenceControllersRef.current.delete(pollIdentity)
        pollGuardsRef.current.delete(pollIdentity)
      }
    },
    [invalidateRefresh, publish, tombstone]
  )

  // Lifecycle/generation guards are imperative cancellation tokens, not mirrors of reactive state.
  // eslint-disable-next-line no-restricted-syntax
  useEffect(() => {
    mountedRef.current = true
    const generation = ++generationRef.current
    abortCadences()
    operationsRef.current = new Map()
    intentGuardsRef.current.clear()
    pollGuardsRef.current.clear()
    invalidatedRef.current.clear()
    setOperations([])
    setErrors({})

    if (!enabled) {
      setIsReconciling(false)

      return () => {
        abortCadences()
        generationRef.current += 1
      }
    }

    setIsReconciling(true)
    void (async () => {
      const refreshOperations: WorkflowMarketplaceOperation[] = []

      for (let offset = 0; offset < RECONCILE_MAX; offset += RECONCILE_LIMIT) {
        const page = await listWorkflowMarketplaceOperations(requestScope, {
          limit: RECONCILE_LIMIT,
          offset
        })

        if (!mountedRef.current || generationRef.current !== generation) {
          return
        }

        refreshOperations.push(
          ...page.operations.filter(operation => operation.kind === 'refresh' && operation.source_name !== null)
        )

        if (page.operations.length < RECONCILE_LIMIT) {
          break
        }
      }

      if (!mountedRef.current || generationRef.current !== generation) {
        return
      }

      operationsRef.current = new Map(refreshOperations.map(operation => [operation.id, operation]))
      setOperations(refreshOperations)

      for (const operation of refreshOperations) {
        if (!terminal(operation)) {
          void poll(operation, generation, requestScope, scopeKey)
        }
      }

      if (refreshOperations.some(terminal)) {
        await invalidateRefresh(scopeKey, `reconcile-${generation}`, generation)
      }
    })()
      .catch(() => {
        if (mountedRef.current && generationRef.current === generation) {
          setErrors({ __reconcile__: 'status' })
        }
      })
      .finally(() => {
        if (mountedRef.current && generationRef.current === generation) {
          setIsReconciling(false)

          if (reconcileGuardRef.current === scopeKey) {
            reconcileGuardRef.current = null
          }
        }
      })

    return () => {
      abortCadences()
      generationRef.current += 1
    }
  }, [abortCadences, enabled, invalidateRefresh, poll, reconcileNonce, requestScope, scopeKey])

  useEffect(
    () => () => {
      mountedRef.current = false
      abortCadences()
      generationRef.current += 1
    },
    [abortCadences]
  )

  const start = useCallback(
    async (
      sourceName: string,
      request: () => Promise<WorkflowMarketplaceOperation>,
      origin: MarketplaceOperationOrigin = { generation: generationRef.current, scopeKey }
    ) => {
      const identity = `${origin.scopeKey}:source:${sourceName}`
      let admitted: null | WorkflowMarketplaceOperation = null

      if (!enabled || !originIsCurrent(origin) || intentGuardsRef.current.has(identity)) {
        return null
      }

      intentGuardsRef.current.add(identity)

      try {
        try {
          admitted = await request()
        } catch {
          if (originIsCurrent(origin)) {
            setErrors(current => ({ ...current, [sourceName]: 'status' }))
          }

          return null
        }

        if (!originIsCurrent(origin)) {
          return null
        }

        if (admitted.kind !== 'refresh' || admitted.source_name !== sourceName) {
          setErrors(current => ({ ...current, [sourceName]: 'status' }))

          return null
        }
      } finally {
        intentGuardsRef.current.delete(identity)
      }

      return admitted === null ? null : poll(admitted, origin.generation, requestScope, origin.scopeKey)
    },
    [enabled, originIsCurrent, poll, requestScope, scopeKey]
  )

  const cancel = useCallback(
    async (operationId: string) => {
      const generation = generationRef.current
      const existing = operationsRef.current.get(operationId)
      const identity = existing?.source_name === null ? null : `${scopeKey}:source:${existing?.source_name}`

      if (
        !enabled ||
        renderedScopeKeyRef.current !== scopeKey ||
        existing === undefined ||
        terminal(existing) ||
        identity === null ||
        intentGuardsRef.current.has(identity)
      ) {
        return null
      }

      intentGuardsRef.current.add(identity)

      try {
        const response = await cancelWorkflowMarketplaceOperation(operationId, requestScope)
        publish(response, generation)

        if (terminal(response)) {
          await invalidateRefresh(scopeKey, response.id, generation)
        }

        return response
      } catch {
        const currentOperation = operationsRef.current.get(operationId)

        if (
          mountedRef.current &&
          generationRef.current === generation &&
          currentOperation?.kind === 'refresh' &&
          currentOperation.source_name !== null
        ) {
          setErrors(current => ({ ...current, [currentOperation.source_name]: 'status' }))
        }

        return null
      } finally {
        intentGuardsRef.current.delete(identity)
      }
    },
    [enabled, invalidateRefresh, publish, requestScope, scopeKey]
  )

  const retry = useCallback(
    async (operationId: string) => {
      const generation = generationRef.current
      const existing = operationsRef.current.get(operationId)
      const identity = existing?.source_name === null ? null : `${scopeKey}:source:${existing?.source_name}`

      if (
        !enabled ||
        renderedScopeKeyRef.current !== scopeKey ||
        existing === undefined ||
        terminal(existing) ||
        identity === null ||
        intentGuardsRef.current.has(identity)
      ) {
        return null
      }

      intentGuardsRef.current.add(identity)
      let next: null | WorkflowMarketplaceOperation = null

      try {
        next = await getWorkflowMarketplaceOperation(operationId, requestScope)
      } catch (error) {
        if (errorCode(error) === 'marketplace_operation_not_found') {
          tombstone(existing, generation)
          await invalidateRefresh(scopeKey, existing.id, generation)
        } else if (mountedRef.current && generationRef.current === generation && existing.source_name !== null) {
          setErrors(current => ({
            ...current,
            [existing.source_name]: 'status'
          }))
        }

        return null
      } finally {
        intentGuardsRef.current.delete(identity)
      }

      return next === null ? null : poll(next, generation, requestScope, scopeKey)
    },
    [enabled, invalidateRefresh, poll, requestScope, scopeKey, tombstone]
  )

  const operationForSource = useCallback(
    (sourceName: string) => {
      const candidates = operations.filter(operation => operation.source_name === sourceName)

      return candidates.sort((left, right) => {
        const leftActive = terminal(left) ? 0 : 1
        const rightActive = terminal(right) ? 0 : 1

        return (
          rightActive - leftActive ||
          right.updated_at.localeCompare(left.updated_at) ||
          right.created_at.localeCompare(left.created_at) ||
          right.id.localeCompare(left.id)
        )
      })[0]
    },
    [operations]
  )

  const reconcile = useCallback(() => {
    if (!enabled || reconcileGuardRef.current === scopeKey) {
      return
    }

    reconcileGuardRef.current = scopeKey
    setReconcileNonce(current => current + 1)
  }, [enabled, scopeKey])

  return useMemo(
    () => ({
      cancel,
      captureOrigin,
      errors,
      isReconciling,
      operationForSource,
      operations,
      originIsCurrent,
      reconcile,
      retry,
      start
    }),
    [
      cancel,
      captureOrigin,
      errors,
      isReconciling,
      operationForSource,
      operations,
      originIsCurrent,
      reconcile,
      retry,
      start
    ]
  )
}
