import type { Query, QueryClient, QueryKey } from '@tanstack/react-query'
import { atom } from 'nanostores'

import { profileScopeKey } from '@/api/client'
import type { LifecycleConnectionBinding } from '@/api/workflow-marketplace-lifecycle'
import type { PackageIdentity, PackageState } from '@/types/workflow-marketplace-lifecycle'

import { decodeMarketplaceOperation } from './workflow-marketplace-codec'
import {
  decodeLifecycleOperation,
  sameLifecycleIdentity,
  sameLifecycleValue
} from './workflow-marketplace-lifecycle-codec'

interface PackageObservation {
  generation: number
  packageState: PackageState | null
}

/** Memory-only freshness evidence. Backend state and operation history keep their separate authorities. */
export function createMarketplaceReconciliation(
  queryClient: QueryClient,
  isCurrent: (binding: LifecycleConnectionBinding) => boolean
) {
  const packages = new Map<string, PackageObservation>()
  const scopes = new Map<string, { binding: LifecycleConnectionBinding; generation: number }>()
  const projections = new WeakMap<Query, { binding: LifecycleConnectionBinding; generation: number; fresh: boolean }>()
  const revision = atom(0)
  const changed = () => revision.set(revision.get() + 1)

  const key = (binding: LifecycleConnectionBinding, identity: PackageIdentity) =>
    JSON.stringify([
      binding.connectionId,
      binding.connectionGeneration,
      binding.profile,
      binding.principalBinding,
      binding.registryEpoch,
      identity.source_key,
      identity.package_id
    ])

  const read = (binding: LifecycleConnectionBinding, identity: PackageIdentity): PackageObservation =>
    packages.get(key(binding, identity)) ?? { generation: 0, packageState: null }

  const off = queryClient.getQueryCache().subscribe(event => {
    if (event.type !== 'updated') {
      return
    }

    const origin = scopes.get(String(event.query.queryKey[1]))

    if (!origin || !isCurrent(origin.binding)) {
      return
    }

    if (event.action.type === 'fetch') {
      projections.set(event.query, { ...origin, fresh: false })
    } else if (event.action.type === 'success' && !event.action.manual) {
      const started = projections.get(event.query)

      if (started && sameLifecycleValue(started.binding, origin.binding) && started.generation === origin.generation) {
        started.fresh = true
        changed()
      }
    }
  })

  function projectionFresh(
    binding: LifecycleConnectionBinding,
    queryKey: QueryKey,
    generation: number,
    identity?: PackageIdentity
  ) {
    if (queryKey[1] !== profileScopeKey(binding) || !isCurrent(binding)) {
      return false
    }

    const query = queryClient.getQueryCache().find({ queryKey, exact: true })
    const receipt = query && projections.get(query)

    if (identity && (queryKey[2] === 'detail' || queryKey[2] === 'operation')) {
      const operation = decodeLifecycleOperation(query?.state.data) ?? decodeMarketplaceOperation(query?.state.data)

      if (
        operation?.state !== 'succeeded' ||
        operation.result.type !== 'package_detail' ||
        !sameLifecycleIdentity(operation.result.value.identity, identity)
      ) {
        return false
      }
    }

    return Boolean(
      receipt?.fresh &&
      sameLifecycleValue(receipt.binding, binding) &&
      receipt.generation >= generation &&
      query?.state.status === 'success' &&
      !query.state.isInvalidated
    )
  }

  return {
    $revision: {
      get: revision.get.bind(revision),
      listen: revision.listen.bind(revision),
      subscribe: revision.subscribe.bind(revision)
    },
    read,
    changed,
    dispose: off,
    bind(binding: LifecycleConnectionBinding) {
      const scope = profileScopeKey(binding)
      const current = scopes.get(scope)

      if (!current || !sameLifecycleValue(current.binding, binding)) {
        scopes.set(scope, { binding, generation: 0 })
      }

      changed()
    },
    projectionFresh,
    scopeGeneration(binding: LifecycleConnectionBinding) {
      return scopes.get(profileScopeKey(binding))?.generation ?? 0
    },
    advance(binding: LifecycleConnectionBinding, identity: PackageIdentity) {
      const scope = profileScopeKey(binding)
      const generation = (scopes.get(scope)?.generation ?? 0) + 1
      scopes.set(scope, { binding, generation })
      packages.set(key(binding, identity), { generation, packageState: null })
      changed()

      const predicate = ({ queryKey }: { queryKey: readonly unknown[] }) =>
        queryKey[1] === scope &&
        (queryKey[0] === 'workflow-catalog' ||
          (queryKey[0] === 'workflow-marketplace' && queryKey[2] !== 'sources' && queryKey[2] !== 'capabilities'))

      // Cancel synchronously before admission dispatch. Invalidation schedules fresh reads without inventing state.
      void queryClient.cancelQueries({ predicate })
      void queryClient.invalidateQueries({ predicate })
    },
    accept(
      binding: LifecycleConnectionBinding,
      identity: PackageIdentity,
      generation: number,
      packageState: PackageState
    ) {
      if (read(binding, identity).generation !== generation) {
        return false
      }

      packages.set(key(binding, identity), { generation, packageState })
      revision.set(revision.get() + 1)

      return true
    }
  }
}
