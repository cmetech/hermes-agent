/** Test-only network fixture harness. Never imported by application modules. */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render } from '@testing-library/react'
import { atom } from 'nanostores'
import type { ReactNode } from 'react'

import {
  LifecycleApiError,
  type LifecycleConnectionBinding,
  type LifecycleStart
} from '@/api/workflow-marketplace-lifecycle'
import { I18nProvider } from '@/i18n'
import { decodeLifecycleOperation, decodeLifecyclePackageState } from '@/lib/workflow-marketplace-lifecycle-codec'
import { createMarketplaceSupervisor, type MarketplaceIntent } from '@/store/workflow-marketplace-supervisor'
import type { LifecycleOperation } from '@/types/workflow-marketplace-lifecycle'

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

export function deferredLifecycle<T>() {
  let resolve!: (value: T) => void

  const promise = new Promise<T>(yes => {
    resolve = yes
  })

  return { promise, resolve }
}

export function createLifecycleHarness() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } })
  const visibility = atom(true)
  const receipts = new Map<string, LifecycleOperation>()
  const listeners = new Set<(scope: typeof lifecycleScope | null, reason: string) => void>()
  let terminalName = 'service install confirm'
  let packageState = lifecycleStateFixture('service absent')
  let stateRead: null | ReturnType<typeof deferredLifecycle<unknown>> = null
  let stateFailure = false
  let evicted = false
  let invalidTerminalEvidence = false
  let principal = corpus.capabilities.principal_binding
  let epoch = corpus.capabilities.registry_epoch
  let afterPost: (() => void) | undefined
  let count = 0
  const now = Date.parse(corpus.capabilities.server_time)

  const supervisor = createMarketplaceSupervisor({
    queryClient,
    visibility,
    clock: () => ({ wallNowMs: now, monotonicNowMs: 0 }),
    connections: {
      resolveConnection: async scope => ({ connectionId: scope.connectionId, connectionGeneration: 1 }),
      isConnected: () => true,
      subscribe: callback => {
        listeners.add(callback)

        return () => listeners.delete(callback)
      }
    },
    api: {
      capabilities: async scope => ({
        ...corpus.capabilities,
        profile: scope.profile,
        principal_binding: principal,
        registry_epoch: epoch
      }),
      list: async () => ({ ...corpus.operationPageFinal, items: [] }),
      start: async (input: LifecycleStart) => {
        const retained = receipts.get(input.requestId)

        if (retained) {
          return retained
        }

        const operation = decodeLifecycleOperation({
          ...lifecycleFixture(terminalName),
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
        afterPost?.()

        return operation
      },
      get: async id => [...receipts.values()].find(item => item.id === id),
      lookup: async id => {
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
      cancel: async id => [...receipts.values()].find(item => item.id === id),
      packageState: async (_identity, binding) => {
        if (stateFailure) {
          throw new LifecycleApiError('marketplace_network_error', 0)
        }

        return stateRead ? stateRead.promise : { ...packageState, profile: binding.profile }
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
    failOriginRefetches: () => {
      stateFailure = true
    },
    state: (name: string, orphaned = false) => {
      stateFailure = false
      packageState = lifecycleStateFixture(name)

      if (orphaned && packageState.installed) {
        packageState = { ...packageState, installed: { ...packageState.installed, orphaned_source: true } }
      }
    },
    holdState: () => {
      stateRead = deferredLifecycle<unknown>()

      return stateRead
    },
    onPost: (callback: () => void) => {
      afterPost = callback
    },
    complete: (name: string) => {
      for (const [id, earlier] of receipts) {
        const value = decodeLifecycleOperation({ ...lifecycleFixture(name), id: earlier.id, request_id: id })

        if (!value) {
          throw new Error('Invalid completion fixture')
        }

        receipts.set(id, value)
      }
    },
    evict: () => {
      evicted = true
    },
    invalidTerminalEvidence: () => {
      invalidTerminalEvidence = true
    },
    changeAuthority: (kind: 'principal' | 'epoch') => {
      if (kind === 'principal') {
        principal = 'c'.repeat(64)
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
