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

type OperationRecovery = 'evicted' | 'status'

function terminal(operation: WorkflowMarketplaceOperation): boolean {
  return operation.state === 'succeeded' || operation.state === 'failed' || operation.state === 'cancelled'
}

function errorCode(error: unknown): null | string {
  return typeof error === 'object' && error !== null && 'code' in error && typeof error.code === 'string'
    ? error.code
    : null
}

function nextCadence(): Promise<void> {
  return new Promise(resolve => {
    const schedule = () => {
      document.removeEventListener('visibilitychange', visible)
      window.setTimeout(resolve, POLL_MS)
    }

    const visible = () => {
      if (document.visibilityState === 'visible') {
        schedule()
      }
    }

    if (document.visibilityState === 'hidden') {
      document.addEventListener('visibilitychange', visible)
    } else {
      schedule()
    }
  })
}

export interface MarketplaceOperationController {
  cancel: (operationId: string) => Promise<null | WorkflowMarketplaceOperation>
  errors: Readonly<Record<string, OperationRecovery>>
  isReconciling: boolean
  operationForSource: (sourceName: string) => WorkflowMarketplaceOperation | undefined
  operations: readonly WorkflowMarketplaceOperation[]
  reconcile: () => void
  retry: (operationId: string) => Promise<null | WorkflowMarketplaceOperation>
  start: (
    sourceName: string,
    request: () => Promise<WorkflowMarketplaceOperation>
  ) => Promise<null | WorkflowMarketplaceOperation>
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
  const mountedRef = useRef(true)
  const startGuardsRef = useRef(new Set<string>())
  const cancelGuardsRef = useRef(new Set<string>())
  const pollGuardsRef = useRef(new Set<string>())
  const invalidatedRef = useRef(new Set<string>())
  const reconcileGuardRef = useRef<null | string>(null)
  const operationsRef = useRef(new Map<string, WorkflowMarketplaceOperation>())
  const [operations, setOperations] = useState<WorkflowMarketplaceOperation[]>([])
  const [errors, setErrors] = useState<Record<string, OperationRecovery>>({})
  const [isReconciling, setIsReconciling] = useState(enabled)
  const [reconcileNonce, setReconcileNonce] = useState(0)

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
          await nextCadence()

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

            if (sourceName !== null) {
              setErrors(existing => ({
                ...existing,
                [sourceName]: errorCode(error) === 'marketplace_operation_not_found' ? 'evicted' : 'status'
              }))
            }

            if (errorCode(error) === 'marketplace_operation_not_found') {
              await invalidateRefresh(originScopeKey, current.id, generation)
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
        pollGuardsRef.current.delete(pollIdentity)
      }
    },
    [invalidateRefresh, publish]
  )

  // Lifecycle/generation guards are imperative cancellation tokens, not mirrors of reactive state.
  // eslint-disable-next-line no-restricted-syntax
  useEffect(() => {
    mountedRef.current = true
    const generation = ++generationRef.current
    operationsRef.current = new Map()
    startGuardsRef.current.clear()
    cancelGuardsRef.current.clear()
    pollGuardsRef.current.clear()
    invalidatedRef.current.clear()
    setOperations([])
    setErrors({})

    if (!enabled) {
      setIsReconciling(false)

      return () => {
        generationRef.current += 1
      }
    }

    setIsReconciling(true)
    void listWorkflowMarketplaceOperations(requestScope, { limit: 100 })
      .then(page => {
        if (!mountedRef.current || generationRef.current !== generation) {
          return
        }

        const refreshOperations = page.operations.filter(
          operation => operation.kind === 'refresh' && operation.source_name !== null
        )

        operationsRef.current = new Map(refreshOperations.map(operation => [operation.id, operation]))
        setOperations(refreshOperations)

        for (const operation of refreshOperations) {
          if (!terminal(operation)) {
            void poll(operation, generation, requestScope, scopeKey)
          }
        }
      })
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
      generationRef.current += 1
    }
  }, [enabled, poll, reconcileNonce, requestScope, scopeKey])

  useEffect(
    () => () => {
      mountedRef.current = false
      generationRef.current += 1
    },
    []
  )

  const start = useCallback(
    async (sourceName: string, request: () => Promise<WorkflowMarketplaceOperation>) => {
      const generation = generationRef.current
      const identity = `${scopeKey}:${sourceName}`

      if (!enabled || startGuardsRef.current.has(identity)) {
        return null
      }

      startGuardsRef.current.add(identity)

      try {
        let admitted: WorkflowMarketplaceOperation

        try {
          admitted = await request()
        } catch {
          if (mountedRef.current && generationRef.current === generation) {
            setErrors(current => ({ ...current, [sourceName]: 'status' }))
          }

          return null
        }

        if (admitted.kind !== 'refresh' || admitted.source_name !== sourceName) {
          setErrors(current => ({ ...current, [sourceName]: 'status' }))

          return null
        }

        return await poll(admitted, generation, requestScope, scopeKey)
      } finally {
        startGuardsRef.current.delete(identity)
      }
    },
    [enabled, poll, requestScope, scopeKey]
  )

  const cancel = useCallback(
    async (operationId: string) => {
      const generation = generationRef.current
      const identity = `${scopeKey}:${operationId}`

      if (!enabled || cancelGuardsRef.current.has(identity)) {
        return null
      }

      cancelGuardsRef.current.add(identity)

      try {
        const response = await cancelWorkflowMarketplaceOperation(operationId, requestScope)
        publish(response, generation)

        if (terminal(response)) {
          await invalidateRefresh(scopeKey, response.id, generation)
        }

        return response
      } catch {
        const existing = operationsRef.current.get(operationId)

        if (
          mountedRef.current &&
          generationRef.current === generation &&
          existing?.kind === 'refresh' &&
          existing.source_name !== null
        ) {
          setErrors(current => ({ ...current, [existing.source_name]: 'status' }))
        }

        return null
      } finally {
        cancelGuardsRef.current.delete(identity)
      }
    },
    [enabled, invalidateRefresh, publish, requestScope, scopeKey]
  )

  const retry = useCallback(
    async (operationId: string) => {
      const generation = generationRef.current
      const existing = operationsRef.current.get(operationId)

      if (!enabled || existing === undefined || terminal(existing)) {
        return null
      }

      try {
        const next = await getWorkflowMarketplaceOperation(operationId, requestScope)

        return poll(next, generation, requestScope, scopeKey)
      } catch (error) {
        if (mountedRef.current && generationRef.current === generation && existing.source_name !== null) {
          setErrors(current => ({
            ...current,
            [existing.source_name]: errorCode(error) === 'marketplace_operation_not_found' ? 'evicted' : 'status'
          }))
        }

        return null
      }
    },
    [enabled, poll, requestScope, scopeKey]
  )

  const operationForSource = useCallback(
    (sourceName: string) =>
      operations.find(
        operation =>
          operation.source_name === sourceName && (operation.state === 'pending' || operation.state === 'running')
      ) ?? operations.find(operation => operation.source_name === sourceName),
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
    () => ({ cancel, errors, isReconciling, operationForSource, operations, reconcile, retry, start }),
    [cancel, errors, isReconciling, operationForSource, operations, reconcile, retry, start]
  )
}
