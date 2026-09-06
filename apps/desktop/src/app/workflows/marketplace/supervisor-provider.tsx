import type { QueryClient, QueryKey } from '@tanstack/react-query'
import { atom } from 'nanostores'
import { createContext, type ReactNode, useContext, useSyncExternalStore } from 'react'

import { getApiRequestConnection, getApiRequestProfile, type HermesGateway, type ProfileScope } from '@/api/client'
import * as lifecycle from '@/api/workflow-marketplace-lifecycle'
import { $gateway } from '@/store/gateway'
import { $activeGatewayProfile } from '@/store/profile'
import { $connection } from '@/store/session'
import { isAuxiliaryWindow } from '@/store/windows'
import { createMarketplaceSupervisor, type MarketplaceScope } from '@/store/workflow-marketplace-supervisor'
import type { PackageIdentity } from '@/types/workflow-marketplace-lifecycle'

type Supervisor = ReturnType<typeof createMarketplaceSupervisor>
const MarketplaceSupervisorContext = createContext<Supervisor | null>(null)
interface SupervisorProviderProps {
  children?: ReactNode
  supervisor: Supervisor | null
}

/** The main window owns the instance; StrictMode and navigation own only subscriptions. */
export function MarketplaceSupervisorProvider({ children, supervisor }: SupervisorProviderProps) {
  return <MarketplaceSupervisorContext value={supervisor}>{children}</MarketplaceSupervisorContext>
}

export function useMarketplaceSupervisor() {
  const supervisor = useContext(MarketplaceSupervisorContext)

  if (!supervisor) {
    throw new Error('Marketplace supervisor is unavailable.')
  }

  return supervisor
}

const noSubscription = () => () => undefined
const noRevision = () => 0

/** Optional access is limited to read-only presentation; mutation adapters use the strict hook above. */
export function useMarketplaceReadOnlyScope(input: ProfileScope) {
  const scope =
    typeof input === 'object' && input !== null
      ? { connectionId: input.connectionId ?? null, profile: input.profile ?? 'default' }
      : { connectionId: null, profile: input ?? 'default' }

  const supervisor = useContext(MarketplaceSupervisorContext)

  const revision = useSyncExternalStore(
    supervisor?.reconciliation.$revision.subscribe ?? noSubscription,
    supervisor?.reconciliation.$revision.get ?? noRevision
  )

  return readOnlySnapshot(supervisor, scope, revision)
}

// Bind imperative reads to this subscription snapshot; React Compiler must not reuse a prior gate.
function readOnlySnapshot(supervisor: Supervisor | null, scope: MarketplaceScope, revision: number) {
  const state = supervisor?.bindings.state(scope)
  const binding = state && 'binding' in state ? state.binding : null

  return {
    supervisor,
    binding,
    revision,
    quarantined: Boolean(supervisor && !binding),
    packageGate: (identity: PackageIdentity, projection?: QueryKey) =>
      supervisor && binding ? supervisor.getPackageGate(binding, identity, projection) : null,
    packageGeneration: (identity: PackageIdentity) =>
      supervisor && binding ? supervisor.reconciliation.read(binding, identity).generation : 0,
    canUseCatalog: (projection: QueryKey) =>
      !supervisor || Boolean(binding && supervisor.canUseCatalog(binding, projection))
  }
}

const applications = new WeakMap<QueryClient, Supervisor>()

/** Main-window lifetime, deliberately outside React's mount/effect cycle. */
export function startMainWindowMarketplaceSupervision(queryClient: QueryClient): Supervisor | null {
  if (isAuxiliaryWindow()) {
    return null
  }

  const existing = applications.get(queryClient)

  if (existing) {
    return existing
  }

  const visibility = atom(document.visibilityState !== 'hidden')
  const sockets = new Map<string, { gateway: HermesGateway; unsubscribe: () => void }>()
  const key = (scope: MarketplaceScope) => JSON.stringify([scope.connectionId, scope.profile])
  let stopped = false
  let notify: (scope: MarketplaceScope | null, reason: string) => void = () => undefined

  const supervisor = createMarketplaceSupervisor({
    queryClient,
    visibility,
    clock: () => ({ wallNowMs: Date.now(), monotonicNowMs: performance.now() }),
    api: {
      capabilities: lifecycle.getLifecycleCapabilities,
      start: lifecycle.startLifecycleOperation,
      get: lifecycle.getLifecycleOperation,
      lookup: lifecycle.lookupLifecycleAdmission,
      list: lifecycle.listLifecycleOperations,
      cancel: lifecycle.cancelLifecycleOperation,
      packageState: lifecycle.getLifecyclePackageState
    },
    connections: {
      resolveConnection: async scope => {
        if (scope.connectionId !== null && !window.hermesDesktop.getConnectionFor) {
          throw new lifecycle.LifecycleApiError('marketplace_lifecycle_unsupported', 0)
        }

        const descriptor =
          scope.connectionId === null
            ? await window.hermesDesktop.getConnection(scope.profile)
            : await window.hermesDesktop.getConnectionFor!({ connectionId: scope.connectionId, profile: scope.profile })

        return { connectionId: descriptor.connectionId ?? null, connectionGeneration: descriptor.connectionGeneration }
      },
      isConnected: scope => sockets.get(key(scope))?.gateway.connectionState === 'open',
      subscribe: callback => {
        notify = callback

        return () => {
          notify = () => undefined
        }
      }
    }
  })

  const offNative = window.hermesDesktop?.onConnectionGenerationChanged?.(event => {
    if (event.kind === 'exhausted') {
      notify(null, 'marketplace_connection_generation_exhausted')
    } else {
      notify(event, 'reconfigured')
      void supervisor.reconcileScope(event)
    }
  })

  function attachActive() {
    if (stopped) {
      return
    }

    const gateway = $gateway.get()

    if (!gateway) {
      return
    }

    const scope = { connectionId: getApiRequestConnection(), profile: getApiRequestProfile() ?? 'default' }
    const previous = sockets.get(key(scope))

    if (previous?.gateway === gateway) {
      return
    }

    previous?.unsubscribe()

    if (previous) {
      notify(scope, 'reconfigured')
    }

    const socket = { gateway, unsubscribe: () => undefined as void }
    sockets.set(key(scope), socket)
    socket.unsubscribe = gateway.onState(state => {
      if (state === 'open') {
        void supervisor.reconcileScope(scope)
      } else {
        notify(scope, 'disconnect')
      }
    })
  }

  // Read the completed explicit route after the routing stores settle together.
  const changed = () => queueMicrotask(attachActive)
  const offActive = [$gateway.listen(changed), $connection.listen(changed), $activeGatewayProfile.listen(changed)]
  const visible = () => visibility.set(document.visibilityState !== 'hidden')
  document.addEventListener('visibilitychange', visible)
  const dispose = supervisor.dispose

  supervisor.dispose = () => {
    if (stopped) {
      return
    }

    stopped = true
    dispose()
    offNative?.()
    offActive.forEach(off => off())

    for (const socket of sockets.values()) {
      socket.unsubscribe()
    }

    sockets.clear()
    document.removeEventListener('visibilitychange', visible)
    window.removeEventListener('pagehide', supervisor.dispose)
    applications.delete(queryClient)
  }

  window.addEventListener('pagehide', supervisor.dispose)
  applications.set(queryClient, supervisor)
  attachActive()

  return supervisor
}
