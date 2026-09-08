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
interface PackageReadTicket {
  generation: number
  order: number
}

function inspectionOperation(value: unknown, binding: LifecycleConnectionBinding) {
  const operation = decodeLifecycleOperation(value) ?? decodeMarketplaceOperation(value)

  if (!operation || operation.profile !== binding.profile) {
    return null
  }

  if (operation.schema_version === 2) {
    return operation.kind === 'inspect' &&
      operation.registry_epoch === binding.registryEpoch &&
      operation.subject.type === 'package'
      ? operation
      : null
  }

  return operation.kind === 'package_detail' ? operation : null
}

function inspectionCorrelation(operation: NonNullable<ReturnType<typeof inspectionOperation>>) {
  return operation.schema_version === 2
    ? {
        id: operation.id,
        profile: operation.profile,
        kind: operation.kind,
        schema_version: 2,
        request_id: operation.request_id,
        registry_epoch: operation.registry_epoch,
        subject: operation.subject,
        selection: operation.selection
      }
    : {
        id: operation.id,
        profile: operation.profile,
        kind: operation.kind,
        schema_version: 1,
        source_name: operation.source_name
      }
}

/** Memory-only freshness evidence. Backend state and operation history keep their separate authorities. */
export function createMarketplaceReconciliation(
  queryClient: QueryClient,
  isCurrent: (binding: LifecycleConnectionBinding) => boolean
) {
  const packages = new Map<string, { binding: LifecycleConnectionBinding; observation: PackageObservation }>()
  const packageReads = new Map<string, number>()
  let packageReadOrder = 0
  const scopes = new Map<string, { binding: LifecycleConnectionBinding; generation: number }>()
  const projections = new WeakMap<Query, { binding: LifecycleConnectionBinding; generation: number; fresh: boolean }>()

  // Application-lifetime admission evidence, independent of query retention and later GET/list fetches.
  const inspections = new Map<
    string,
    { generation: number; queryKey: QueryKey; correlation: ReturnType<typeof inspectionCorrelation> }
  >()

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
    packages.get(key(binding, identity))?.observation ?? { generation: 0, packageState: null }

  const inspectionKey = (binding: LifecycleConnectionBinding, id: string) =>
    JSON.stringify([
      binding.connectionId,
      binding.connectionGeneration,
      binding.profile,
      binding.principalBinding,
      binding.registryEpoch,
      id
    ])

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
        const queryKey = event.query.queryKey

        if (queryKey[0] === 'workflow-marketplace' && queryKey[2] === 'detail') {
          const operation = inspectionOperation(event.query.state.data, started.binding)

          if (operation) {
            const id = inspectionKey(started.binding, operation.id)

            if (!inspections.has(id)) {
              inspections.set(id, {
                generation: started.generation,
                queryKey: [...queryKey],
                correlation: inspectionCorrelation(operation)
              })
            }
          }
        }

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
      const operation = inspectionOperation(query?.state.data, binding)
      const admission = operation && inspections.get(inspectionKey(binding, operation.id))

      if (
        operation?.state !== 'succeeded' ||
        operation.result.type !== 'package_detail' ||
        !sameLifecycleIdentity(operation.result.value.identity, identity) ||
        !admission ||
        admission.generation < generation ||
        admission.queryKey[3] !== identity.source_key ||
        admission.queryKey[4] !== identity.package_id ||
        !sameLifecycleValue(admission.correlation, inspectionCorrelation(operation)) ||
        (queryKey[2] === 'operation' ? queryKey[3] !== operation.id : !sameLifecycleValue(queryKey, admission.queryKey))
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

  function advance(binding: LifecycleConnectionBinding, identity: PackageIdentity) {
    const scope = profileScopeKey(binding)
    const generation = (scopes.get(scope)?.generation ?? 0) + 1
    scopes.set(scope, { binding, generation })
    packages.set(key(binding, identity), { binding, observation: { generation, packageState: null } })
    changed()

    const predicate = ({ queryKey }: { queryKey: readonly unknown[] }) =>
      queryKey[1] === scope &&
      (queryKey[0] === 'workflow-catalog' ||
        (queryKey[0] === 'workflow-marketplace' && queryKey[2] !== 'sources' && queryKey[2] !== 'capabilities'))

    // Cancel synchronously before admission dispatch. Invalidation schedules fresh reads without inventing state.
    void queryClient.cancelQueries({ predicate })
    void queryClient.invalidateQueries({ predicate })
  }

  function scopeState(binding: LifecycleConnectionBinding) {
    const states = [...packages.values()]
      .filter(value => sameLifecycleValue(value.binding, binding))
      .map(value => value.observation.packageState)

    if (states.some(state => state?.busy)) {
      return 'busy'
    }

    if (states.some(state => state?.recovery === 'required')) {
      return 'recovery_required'
    }

    if (states.some(state => !state || state.state === 'unconfirmed' || state.recovery !== 'clear')) {
      return 'unknown'
    }

    return 'clear'
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
    scopeState,
    scopeGeneration(binding: LifecycleConnectionBinding) {
      return scopes.get(profileScopeKey(binding))?.generation ?? 0
    },
    advance,
    beginPackageRead(binding: LifecycleConnectionBinding, identity: PackageIdentity): PackageReadTicket {
      const ticket = { generation: read(binding, identity).generation, order: ++packageReadOrder }
      // The latest-started exact read owns publication even if it fails; older responses cannot fill behind it.
      packageReads.set(key(binding, identity), ticket.order)

      return ticket
    },
    finishPackageRead(binding: LifecycleConnectionBinding, identity: PackageIdentity, ticket: PackageReadTicket) {
      const packageKey = key(binding, identity)

      if (packageReads.get(packageKey) === ticket.order) {
        packageReads.delete(packageKey)
      }
    },
    accept(
      binding: LifecycleConnectionBinding,
      identity: PackageIdentity,
      ticket: PackageReadTicket,
      packageState: PackageState
    ) {
      const previous = read(binding, identity)

      if (previous.generation !== ticket.generation || packageReads.get(key(binding, identity)) !== ticket.order) {
        return false
      }

      const unsafe = packageState.busy || packageState.recovery !== 'clear' || packageState.state === 'unconfirmed'

      // A new unsafe fact fences prior projections, even when no mutation record belongs to this renderer.
      // Repeated identical unsafe classifications must not create a read/advance loop.
      if (
        unsafe &&
        (previous.packageState?.busy !== packageState.busy ||
          previous.packageState?.recovery !== packageState.recovery ||
          previous.packageState?.state !== packageState.state)
      ) {
        advance(binding, identity)
      }

      packages.set(key(binding, identity), {
        binding,
        observation: { generation: read(binding, identity).generation, packageState }
      })
      revision.set(revision.get() + 1)

      return true
    }
  }
}
