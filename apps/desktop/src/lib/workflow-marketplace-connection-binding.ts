import type { QueryClient } from '@tanstack/react-query'

import { profileScopeKey } from '@/api/client'
import type { LifecycleConnectionBinding, LifecycleScope } from '@/api/workflow-marketplace-lifecycle'
import { marketplaceKeys } from '@/app/workflows/marketplace/query-keys'

import { decodeConnectionGenerationEvent } from '../../electron/connection-generation-event'

import { decodeLifecycleCapabilities, sameLifecycleValue } from './workflow-marketplace-lifecycle-codec'

interface MarketplaceScope {
  connectionId: string | null
  profile: string
}

interface DescriptorIdentity {
  connectionId?: string | null
  connectionGeneration?: number
}

export type MarketplaceBindingState =
  | { kind: 'unavailable' | 'probing' | 'unsupported' | 'restart_required' }
  | { kind: 'bound' | 'last_observed'; binding: LifecycleConnectionBinding }

interface BindingAdapters {
  resolveConnection: (scope: MarketplaceScope) => Promise<DescriptorIdentity>
  getCapabilities: (scope: LifecycleScope) => Promise<unknown>
  queries: Pick<QueryClient, 'cancelQueries' | 'removeQueries'>
  changed?: (scope: MarketplaceScope | null) => void
}

interface ScopeEntry {
  scope: MarketplaceScope
  state: MarketplaceBindingState
  lastBinding?: LifecycleConnectionBinding
  explicitLegacyUnsupported?: boolean
  attempt: object
}

/** Memory-only privacy transition coordinator. Operation records/barriers belong to its future supervisor consumer. */
export function createMarketplaceBindingCoordinator(adapters: BindingAdapters) {
  const entries = new Map<string, ScopeEntry>()
  let exhausted = false

  function entry(scope: MarketplaceScope): ScopeEntry {
    const key = profileScopeKey(scope)
    let current = entries.get(key)

    if (!current) {
      current = {
        scope: { connectionId: scope.connectionId, profile: scope.profile },
        state: { kind: 'unavailable' },
        attempt: {}
      }
      entries.set(key, current)
    }

    return current
  }

  function quarantine(
    current: ScopeEntry,
    kind: 'unavailable' | 'probing' | 'unsupported' | 'restart_required',
    explicitLegacyUnsupported = false
  ) {
    current.attempt = {}
    current.explicitLegacyUnsupported = explicitLegacyUnsupported
    current.state = { kind }
    adapters.changed?.(current.scope)

    // QueryClient cancellation prevents even signal-ignoring old query functions from repopulating the root.
    return adapters.queries.cancelQueries({ queryKey: marketplaceKeys.root(profileScopeKey(current.scope)) })
  }

  function exhaust() {
    if (exhausted) {
      return
    }

    exhausted = true

    for (const current of entries.values()) {
      void quarantine(current, 'restart_required')
    }

    adapters.changed?.(null)
  }

  function state(scope: MarketplaceScope): MarketplaceBindingState {
    return exhausted ? { kind: 'restart_required' } : entry(scope).state
  }

  function isCurrent(binding: LifecycleConnectionBinding) {
    const current = state(binding)

    return 'binding' in current && sameLifecycleValue(current.binding, binding)
  }

  return {
    state,
    isCurrent,
    legacyReadAttempt(scope: MarketplaceScope): object | null {
      const current = entry(scope)

      return !exhausted &&
        current.state.kind === 'unsupported' &&
        current.explicitLegacyUnsupported &&
        !current.lastBinding
        ? current.attempt
        : null
    },
    presentation<T>(scope: MarketplaceScope, data: T): T | undefined {
      return 'binding' in state(scope) ? data : undefined
    },
    publish(binding: LifecycleConnectionBinding, commit: () => void) {
      if (!isCurrent(binding)) {
        return false
      }

      commit()

      return true
    },
    notify(value: unknown) {
      const event = decodeConnectionGenerationEvent(value)

      if (event?.kind === 'exhausted') {
        exhaust()
      } else if (event?.kind === 'scope_invalidated') {
        void quarantine(entry(event), 'unavailable')
      }
    },
    suspend(scope: MarketplaceScope, reason: string) {
      if (reason === 'marketplace_connection_generation_exhausted') {
        exhaust()
      } else if (!exhausted) {
        void quarantine(entry(scope), 'unavailable')
      }
    },
    async probe(scope: MarketplaceScope): Promise<LifecycleConnectionBinding | null> {
      if (exhausted) {
        return null
      }

      const current = entry(scope)
      const cancellation = quarantine(current, 'probing')
      const attempt = current.attempt
      const active = () => !exhausted && current.attempt === attempt
      let resolvedGeneration: number | null = null

      try {
        await cancellation

        if (!active()) {
          return null
        }

        const descriptor = await adapters.resolveConnection(current.scope)

        if (!active()) {
          return null
        }

        const generation = descriptor.connectionGeneration

        if (typeof generation !== 'number' || !Number.isSafeInteger(generation) || generation <= 0) {
          void quarantine(current, 'unsupported')

          return null
        }

        if ((descriptor.connectionId ?? null) !== current.scope.connectionId) {
          void quarantine(current, 'unavailable')

          return null
        }

        resolvedGeneration = generation

        const capabilities = decodeLifecycleCapabilities(
          await adapters.getCapabilities({ ...current.scope, connectionGeneration: generation })
        )

        if (!active()) {
          return null
        }

        if (!capabilities || capabilities.profile !== current.scope.profile) {
          void quarantine(current, 'unsupported')

          return null
        }

        const latest = await adapters.resolveConnection(current.scope)

        if (!active()) {
          return null
        }

        if (
          (latest.connectionId ?? null) !== current.scope.connectionId ||
          latest.connectionGeneration !== generation
        ) {
          void quarantine(current, 'unavailable')

          return null
        }

        const binding: LifecycleConnectionBinding = Object.freeze({
          ...current.scope,
          connectionGeneration: generation,
          principalBinding: capabilities.principal_binding,
          registryEpoch: capabilities.registry_epoch
        })

        const old = current.lastBinding

        const sameAuthority =
          old &&
          old.connectionId === binding.connectionId &&
          old.profile === binding.profile &&
          old.principalBinding === binding.principalBinding &&
          old.registryEpoch === binding.registryEpoch

        if (!sameAuthority) {
          await adapters.queries.cancelQueries({ queryKey: marketplaceKeys.root(profileScopeKey(current.scope)) })

          if (!active()) {
            return null
          }

          adapters.queries.removeQueries({ queryKey: marketplaceKeys.root(profileScopeKey(current.scope)) })
        }

        current.lastBinding = binding
        current.state = { kind: sameAuthority ? 'last_observed' : 'bound', binding }
        adapters.changed?.(current.scope)

        return binding
      } catch (error) {
        if (!active()) {
          return null
        }

        const code =
          error && typeof error === 'object' ? Object.getOwnPropertyDescriptor(error, 'code')?.value : undefined

        const message = error instanceof Error ? error.message : ''

        if (
          code === 'marketplace_connection_generation_exhausted' ||
          /^(?:Error invoking remote method 'hermes:connection(?::for)?': Error: )?marketplace_connection_generation_exhausted$/.test(
            message
          )
        ) {
          exhaust()
        } else {
          let explicitLegacyUnsupported = false

          if (resolvedGeneration !== null && code === 'marketplace_lifecycle_unsupported') {
            try {
              const latest = await adapters.resolveConnection(current.scope)
              explicitLegacyUnsupported =
                (latest.connectionId ?? null) === current.scope.connectionId &&
                latest.connectionGeneration === resolvedGeneration
            } catch {
              // A failed identity recheck cannot authorize legacy reads.
            }
          }

          if (!active()) {
            return null
          }

          void quarantine(
            current,
            code === 'marketplace_lifecycle_unsupported' ? 'unsupported' : 'unavailable',
            explicitLegacyUnsupported
          )
        }

        return null
      }
    }
  }
}
