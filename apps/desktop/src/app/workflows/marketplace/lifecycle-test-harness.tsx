/** Test-only network fixture harness. Never imported by application modules. */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { atom } from 'nanostores'
import type { ReactNode } from 'react'

import {
  LifecycleApiError,
  type LifecycleClockSample,
  type LifecycleConnectionBinding,
  type LifecycleStart
} from '@/api/workflow-marketplace-lifecycle'
import { I18nProvider } from '@/i18n'
import { decodeMarketplaceOperation } from '@/lib/workflow-marketplace-codec'
import { decodeLifecycleOperation, decodeLifecyclePackageState } from '@/lib/workflow-marketplace-lifecycle-codec'
import { createMarketplaceSupervisor, type MarketplaceIntent } from '@/store/workflow-marketplace-supervisor'
import type { LifecycleOperation } from '@/types/workflow-marketplace-lifecycle'

import inspectionCorpus from '../../../../../../tests/fixtures/workflow-marketplace-inspection-v1.json'
import corpus from '../../../../../../tests/fixtures/workflow-marketplace-lifecycle-v2.json'

import { MarketplaceSupervisorProvider } from './supervisor-provider'

export const lifecycleScope = { connectionId: 'remote-a', profile: 'support' }
export const lifecycleIdentity = { source_key: 'company', package_id: 'laptop-support' }

export function lifecycleFixture(name: string) {
  const operation = decodeLifecycleOperation(corpus.operationCases.find(item => item.name === name)?.value)

  if (!operation) {
    throw new Error(`Invalid operation fixture: ${name}`)
  }

  return operation
}

export function lifecycleStateFixture(name: string) {
  const state = decodeLifecyclePackageState(corpus.packageStateCases.find(item => item.name === name)?.value)

  if (!state) {
    throw new Error(`Invalid state fixture: ${name}`)
  }

  return state
}

/** Genuine V1 admission fixture; only the pending shell removes terminal fields. */
export function legacyInspectionFixture(name: 'service inspect' | 'service inspect pending', operationId?: string) {
  const original = inspectionCorpus.operationCases[0].value
  const value = operationId ? { ...original, id: operationId } : original

  const operation = decodeMarketplaceOperation(
    name === 'service inspect'
      ? value
      : {
          ...value,
          started_at: null,
          finished_at: null,
          state: 'pending',
          phase: 'queued',
          progress: 0,
          result: null,
          error: null
        }
  )

  if (!operation) {
    throw new Error('Invalid legacy inspection fixture')
  }

  return operation
}

export function deferredLifecycle<T>() {
  let resolve!: (value: T) => void

  const promise = new Promise<T>(yes => {
    resolve = yes
  })

  return { promise, resolve }
}

export function createLifecycleHarness(clock?: () => LifecycleClockSample) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  const visibility = atom(true)
  const receipts = new Map<string, LifecycleOperation>()
  const listeners = new Set<(scope: typeof lifecycleScope | null, reason: string) => void>()
  let terminalName = 'service install confirm'
  let packageState = lifecycleStateFixture('service absent')
  let stateRead: null | ReturnType<typeof deferredLifecycle<unknown>> = null
  const queuedStateReads: Array<ReturnType<typeof deferredLifecycle<unknown>>> = []
  let packageStateCalls = 0
  let stateFailure = false
  let evicted = false
  let invalidTerminalEvidence = false
  let principal = corpus.capabilities.principal_binding
  let epoch = corpus.capabilities.registry_epoch
  let connectionGeneration = 1
  let afterPost: { callback: () => void; kind: LifecycleOperation['kind'] | null } | undefined
  let count = 0
  const routes = new Map<LifecycleOperation['kind'], string>()
  const routeSequences = new Map<LifecycleOperation['kind'], string[]>()
  const rawRoutes = new Map<LifecycleOperation['kind'], string>()
  const oneShotRawRoutes = new Set<LifecycleOperation['kind']>()
  const rawRouteValues = new Map<LifecycleOperation['kind'], unknown>()
  const calls: Array<{ type: string; input?: LifecycleStart; id?: string }> = []
  let getFailure: LifecycleApiError | null = null

  let admission: {
    deferred: ReturnType<typeof deferredLifecycle<void>>
    kind: LifecycleOperation['kind'] | null
  } | null = null

  const kindAdmissions = new Map<LifecycleOperation['kind'], Array<ReturnType<typeof deferredLifecycle<void>>>>()
  let loseResponse: LifecycleOperation['kind'] | null | false = false
  let declared = corpus.capabilities.capabilities
  let mismatch: LifecycleOperation['kind'] | null | false = false
  let capabilityWait: ReturnType<typeof deferredLifecycle<void>> | null = null
  let capabilityFailure: LifecycleApiError['code'] | false = false
  const capabilityCalls: Array<{ connectionId: string | null; profile: string; connectionGeneration: number }> = []
  const now = Date.parse(corpus.capabilities.server_time)

  const supervisor = createMarketplaceSupervisor({
    queryClient,
    visibility,
    clock: clock ?? (() => ({ wallNowMs: now, monotonicNowMs: 0 })),
    connections: {
      resolveConnection: async scope => ({ connectionId: scope.connectionId, connectionGeneration }),
      isConnected: () => true,
      subscribe: callback => {
        listeners.add(callback)

        return () => listeners.delete(callback)
      }
    },
    api: {
      capabilities: async scope => {
        capabilityCalls.push(scope)
        await capabilityWait?.promise

        if (capabilityFailure) {
          throw new LifecycleApiError(capabilityFailure, 0)
        }

        return {
          ...corpus.capabilities,
          ...(clock
            ? {
                server_time: new Date(clock().wallNowMs)
                  .toISOString()
                  .replace('.000Z', 'Z')
                  .replace(/(\.\d{3})Z$/, '$1000Z')
              }
            : {}),
          profile: scope.profile,
          principal_binding: principal,
          registry_epoch: epoch,
          capabilities: declared
        }
      },
      list: async () => {
        calls.push({ type: 'list' })

        return { ...corpus.operationPageFinal, items: [] }
      },
      start: async (input: LifecycleStart) => {
        calls.push({ type: 'start', input })
        const retained = receipts.get(input.requestId)

        if (retained) {
          return retained
        }

        const rawName = rawRoutes.get(input.kind)
        const rawValue = rawRouteValues.get(input.kind)

        if (rawName || rawValue) {
          if (oneShotRawRoutes.delete(input.kind)) {
            rawRoutes.delete(input.kind)
            rawRouteValues.delete(input.kind)
          }

          const raw = rawValue ?? corpus.operationCases.find(item => item.name === rawName)?.value

          if (!raw) {
            throw new Error(`Unknown raw lifecycle fixture: ${rawName}`)
          }

          const correlated = {
            ...raw,
            request_id: input.requestId,
            id: `wmop_aaaaaaaaaaaa_${(++count).toString(16).padStart(32, '0')}`
          }

          const decoded = decodeLifecycleOperation(correlated)

          if (decoded) {
            receipts.set(input.requestId, decoded)

            if (decoded.outcome && 'package_state' in decoded.outcome && decoded.outcome.package_state) {
              packageState = decoded.outcome.package_state
            }
          }

          return correlated
        }

        const defaultFixture =
          input.kind === 'inspect' ? 'service inspect' : input.kind === 'refresh' ? 'service refresh' : terminalName

        const nextRoute = routeSequences.get(input.kind)?.shift()
        const fixture = lifecycleFixture(nextRoute ?? routes.get(input.kind) ?? defaultFixture)

        const operation = decodeLifecycleOperation({
          ...fixture,
          ...(input.kind === 'refresh'
            ? {
                subject: input.subject,
                result:
                  fixture.result?.type === 'source_refresh' && input.subject.type === 'source'
                    ? {
                        ...fixture.result,
                        value: { ...fixture.result.value, source_name: input.subject.source_name }
                      }
                    : fixture.result
              }
            : {}),
          ...(invalidTerminalEvidence
            ? {
                outcome: { type: 'outcome_unknown', reason: 'terminal_invalid' },
                error: { code: 'marketplace_operation_failed', message: 'Workflow marketplace operation failed.' }
              }
            : {}),
          request_id: input.requestId,
          id: `wmop_aaaaaaaaaaaa_${(++count).toString(16).padStart(32, '0')}`
        })

        if (!operation) {
          throw new Error('Invalid correlated fixture')
        }

        receipts.set(input.requestId, operation)

        if (
          routes.size &&
          operation.outcome &&
          'package_state' in operation.outcome &&
          operation.outcome.package_state
        ) {
          packageState = operation.outcome.package_state
        }

        if (afterPost && (afterPost.kind === null || afterPost.kind === input.kind)) {
          afterPost.callback()
        }

        const kindAdmission = kindAdmissions.get(input.kind)?.shift()

        if (kindAdmission) {
          await kindAdmission.promise
        } else if (admission && (admission.kind === null || admission.kind === input.kind)) {
          await admission.deferred.promise
        }

        if (loseResponse === null || loseResponse === input.kind) {
          throw new LifecycleApiError('marketplace_network_error', 0)
        }

        return mismatch === null || mismatch === input.kind
          ? { ...operation, subject: { type: 'package', identity: { source_key: 'company', package_id: 'other' } } }
          : operation
      },
      get: async id => {
        calls.push({ type: 'get', id })

        if (getFailure) {
          const failure = getFailure
          getFailure = null
          throw failure
        }

        return [...receipts.values()].find(item => item.id === id)
      },
      lookup: async id => {
        calls.push({ type: 'lookup', id })
        const operation = receipts.get(id)

        return evicted && operation
          ? {
              state: 'evicted',
              request_id: id,
              operation_id: operation.id,
              registry_epoch: operation.registry_epoch,
              profile: operation.profile,
              kind: operation.kind,
              subject: operation.subject,
              selection: operation.selection
            }
          : { state: 'found', operation }
      },
      cancel: async id => {
        calls.push({ type: 'cancel', id })
        const earlier = [...receipts.values()].find(item => item.id === id)

        if (!earlier) {
          return null
        }

        const originalName = routes.get(earlier.kind)
        const name = originalName?.replace(/ pending$/, '')

        const fixture = lifecycleFixture(`${name} cancelled`)

        const operation = decodeLifecycleOperation({
          ...fixture,
          ...(earlier.kind === 'refresh' ? { subject: earlier.subject } : {}),
          id,
          request_id: earlier.request_id
        })

        if (!operation) {
          throw new Error('Invalid cancellation fixture')
        }

        receipts.set(earlier.request_id, operation)

        return operation
      },
      packageState: async (_identity, binding) => {
        packageStateCalls++

        if (stateFailure) {
          throw new LifecycleApiError('marketplace_network_error', 0)
        }

        return queuedStateReads.shift()?.promise ?? stateRead?.promise ?? { ...packageState, profile: binding.profile }
      }
    }
  })

  async function bind(scope = lifecycleScope) {
    const binding = await supervisor.reconcileScope(scope)

    if (!binding) {
      throw new Error('Fixture binding failed')
    }

    return binding
  }

  async function mutate(binding: LifecycleConnectionBinding, name = 'service install confirm') {
    terminalName = name
    const operation = lifecycleFixture(name)

    if (operation.subject.type !== 'package') {
      throw new Error('Expected package fixture')
    }

    const body = {
      confirmation_token: 'test-only-confirmation-material-1234567890',
      review_digest: 'a'.repeat(64),
      prepare_operation_id: corpus.operationAId,
      subject: operation.subject,
      selection: operation.selection
    }

    let intent: MarketplaceIntent

    switch (operation.kind) {
      case 'trust_revoke':
        intent = {
          kind: operation.kind,
          subject: operation.subject,
          selection: operation.selection,
          body: { identity: lifecycleIdentity, workflow_name: 'A' }
        }

        break

      case 'install_confirm':

      case 'update_confirm':

      case 'remove_confirm':
        intent = { kind: operation.kind, subject: operation.subject, selection: null, body }

        break

      case 'trust_confirm':
        intent = { kind: operation.kind, subject: operation.subject, selection: operation.selection, body }

        break

      default:
        throw new Error('Expected mutation fixture')
    }

    return supervisor.start(intent, binding)
  }

  function Providers({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>
        <I18nProvider configClient={null} initialLocale="en">
          <MarketplaceSupervisorProvider supervisor={supervisor}>{children}</MarketplaceSupervisorProvider>
        </I18nProvider>
      </QueryClientProvider>
    )
  }

  return {
    supervisor,
    queryClient,
    visibility,
    bind,
    mutate,
    Providers,
    calls,
    capabilityCalls,
    holdCapabilities: () => (capabilityWait = deferredLifecycle<void>()),
    failCapabilities: (code: LifecycleApiError['code'] = 'marketplace_network_error') => {
      capabilityFailure = code
    },
    receipts,
    route: (kind: LifecycleOperation['kind'], name: string) => routes.set(kind, name),
    routeSequence: (kind: LifecycleOperation['kind'], names: string[]) => routeSequences.set(kind, [...names]),
    routeRaw: (kind: LifecycleOperation['kind'], name: string) => rawRoutes.set(kind, name),
    routeRawOnce: (kind: LifecycleOperation['kind'], name: string) => {
      rawRoutes.set(kind, name)
      oneShotRawRoutes.add(kind)
    },
    routeValue: (kind: LifecycleOperation['kind'], value: unknown) => rawRouteValues.set(kind, value),
    capabilities: (values: string[]) => {
      declared = values
    },
    mismatch: (kind?: LifecycleOperation['kind']) => {
      mismatch = kind ?? null
    },
    failStatus: (code: LifecycleApiError['code'] = 'marketplace_network_error', status = 0) => {
      getFailure = new LifecycleApiError(code, status)
    },
    holdAdmission: (kind?: LifecycleOperation['kind']) => {
      const deferred = deferredLifecycle<void>()
      admission = { deferred, kind: kind ?? null }

      return deferred
    },
    holdNextAdmission: (kind: LifecycleOperation['kind']) => {
      const next = deferredLifecycle<void>()
      kindAdmissions.set(kind, [...(kindAdmissions.get(kind) ?? []), next])

      return next
    },
    loseResponse: (kind?: LifecycleOperation['kind']) => {
      loseResponse = kind ?? null
    },
    recoverResponses: () => {
      loseResponse = false
    },
    failOriginRefetches: () => {
      stateFailure = true
    },
    state: (name: string, orphaned = false, busy = false) => {
      stateFailure = false
      packageState = lifecycleStateFixture(name)

      if (busy) {
        packageState = { ...packageState, busy: true }
      }

      if (orphaned && packageState.installed) {
        packageState = { ...packageState, installed: { ...packageState.installed, orphaned_source: true } }
      }
    },
    stateFromOutcome: (name: string) => {
      const outcome = lifecycleFixture(name).outcome

      if (!outcome || !('package_state' in outcome) || !outcome.package_state) {
        throw new Error('Expected authoritative PackageState outcome')
      }

      packageState = outcome.package_state
    },
    holdState: () => {
      stateRead = deferredLifecycle<unknown>()

      return stateRead
    },
    holdNextStateReads: (count: number) => {
      const reads = Array.from({ length: count }, () => deferredLifecycle<unknown>())
      queuedStateReads.push(...reads)

      return reads
    },
    packageStateCalls: () => packageStateCalls,
    onPost: (callback: () => void, kind?: LifecycleOperation['kind']) => {
      afterPost = { callback, kind: kind ?? null }
    },
    complete: (name: string) => {
      for (const [id, earlier] of receipts) {
        if (earlier.kind !== lifecycleFixture(name).kind) {
          continue
        }

        const fixture = lifecycleFixture(name)

        const value = decodeLifecycleOperation({
          ...fixture,
          ...(earlier.kind === 'refresh'
            ? {
                subject: earlier.subject,
                result:
                  fixture.result?.type === 'source_refresh' && earlier.subject.type === 'source'
                    ? {
                        ...fixture.result,
                        value: { ...fixture.result.value, source_name: earlier.subject.source_name }
                      }
                    : fixture.result
              }
            : {}),
          id: earlier.id,
          request_id: id
        })

        if (!value) {
          throw new Error('Invalid completion fixture')
        }

        receipts.set(id, value)

        if (routes.size && value.outcome && 'package_state' in value.outcome && value.outcome.package_state) {
          packageState = value.outcome.package_state
        }
      }
    },
    evict: () => {
      evicted = true
    },
    invalidTerminalEvidence: () => {
      invalidTerminalEvidence = true
    },
    changeAuthority: (kind: 'principal' | 'epoch' | 'generation') => {
      if (kind === 'principal') {
        principal = 'c'.repeat(64)
      } else if (kind === 'generation') {
        connectionGeneration += 1
      } else {
        epoch = 'd'.repeat(32)
      }
    },
    disconnect: () => {
      listeners.forEach(listener => listener(lifecycleScope, 'disconnect'))
    },
    dispose: () => {
      supervisor.dispose()
      queryClient.clear()
    }
  }
}

export function renderLifecycleHarness(node: ReactNode, harness = createLifecycleHarness()) {
  return { harness, ...render(<harness.Providers>{node}</harness.Providers>) }
}
