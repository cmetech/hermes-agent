import {
  LifecycleApiError,
  type LifecycleConnectionBinding,
  type LifecycleCorrelation
} from '@/api/workflow-marketplace-lifecycle'

import { decodeLifecycleOperation, sameLifecycleValue } from './workflow-marketplace-lifecycle-codec'

export function acceptSupervisedOperation(
  value: unknown,
  binding: LifecycleConnectionBinding,
  expected: LifecycleCorrelation & { operationId: string | null }
) {
  const operation = decodeLifecycleOperation(value)

  if (
    !operation ||
    operation.profile !== binding.profile ||
    operation.registry_epoch !== binding.registryEpoch ||
    operation.request_id !== expected.requestId ||
    (expected.operationId !== null && operation.id !== expected.operationId) ||
    operation.kind !== expected.kind ||
    !sameLifecycleValue(operation.subject, expected.subject) ||
    !sameLifecycleValue(operation.selection, expected.selection)
  ) {
    throw new LifecycleApiError('marketplace_invalid_response', 0)
  }

  return operation
}

export function supervisionKey(binding: LifecycleConnectionBinding, requestId: string, operationId: string | null) {
  return JSON.stringify([
    binding.connectionId,
    binding.connectionGeneration,
    binding.profile,
    binding.principalBinding,
    binding.registryEpoch,
    requestId,
    operationId
  ])
}

export function waitForSupervision(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(aborted())

      return
    }

    const cleanup = () => {
      clearTimeout(timer)
      signal.removeEventListener('abort', cancel)
    }

    const cancel = () => {
      cleanup()
      reject(aborted())
    }

    const timer = setTimeout(() => {
      cleanup()
      resolve()
    }, ms)

    signal.addEventListener('abort', cancel)
  })
}

function aborted() {
  return new DOMException('Observation stopped.', 'AbortError')
}

/** Logical abort never frees an IPC slot before physical settlement. */
export function createSupervisionScheduler() {
  let active = 0
  const queued = new Set<() => void>()

  function drain() {
    for (const start of queued) {
      if (active >= 3) {
        break
      }

      queued.delete(start)
      start()
    }
  }

  return {
    run<T>(call: () => Promise<T>, signal: AbortSignal): Promise<T> {
      return new Promise((resolve, reject) => {
        if (signal.aborted) {
          reject(aborted())

          return
        }

        const cleanup = () => signal.removeEventListener('abort', cancel)

        const cancel = () => {
          queued.delete(start)
          cleanup()
          reject(aborted())
        }

        const start = () => {
          if (signal.aborted) {
            cancel()

            return
          }

          active++
          let pending: Promise<T>

          try {
            pending = call()
          } catch (error) {
            pending = Promise.reject(error)
          }

          void pending.then(resolve, reject).finally(() => {
            active--
            cleanup()
            drain()
          })
        }

        signal.addEventListener('abort', cancel)
        queued.add(start)
        drain()
      })
    }
  }
}
