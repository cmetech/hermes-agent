import { type QueryKey, useQueryClient } from '@tanstack/react-query'
import { type RefObject, useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'

import { getLifecycleReviewToken, type LifecycleConnectionBinding } from '@/api/workflow-marketplace-lifecycle'
import { sameLifecycleIdentity, sameLifecycleValue } from '@/lib/workflow-marketplace-lifecycle-codec'
import type { MarketplaceIntent } from '@/store/workflow-marketplace-supervisor'
import type {
  LifecycleOperation,
  PackageIdentity,
  PackageState,
  ReviewTokenResponse,
  TrustReviewProjection,
  WorkflowInventoryItem
} from '@/types/workflow-marketplace-lifecycle'

import { useMarketplaceSupervisor } from './supervisor-provider'
import { TrustReviewDialog, type TrustReviewDialogView, type TrustSelection } from './trust-review-dialog'

interface TrustAttachment {
  requestId: string | null
  failed: boolean
  selection: TrustSelection
  inventory: readonly WorkflowInventoryItem[] | null
  preparation?: LifecycleOperation
  tokenUnavailable?: boolean
}

interface PackageTrustReviewProps {
  binding: LifecycleConnectionBinding
  identity: PackageIdentity
  projection?: QueryKey
  projectionEligible: boolean
  focusFallbackRef: RefObject<HTMLElement | null>
}

function inventoryMap(items: readonly WorkflowInventoryItem[]) {
  const byName = new Map<string, string>()
  const paths = new Set<string>()

  for (const item of items) {
    if (byName.has(item.workflow_name) || paths.has(item.definition_path)) {
      return null
    }

    byName.set(item.workflow_name, item.definition_path)
    paths.add(item.definition_path)
  }

  return byName
}

function sameInventory(a: readonly WorkflowInventoryItem[], b: readonly WorkflowInventoryItem[]) {
  const left = inventoryMap(a)
  const right = inventoryMap(b)

  return Boolean(
    left && right && left.size === right.size && [...left].every(([name, path]) => right.get(name) === path)
  )
}

function sameTrustMap(
  a: readonly { workflow_name: string; definition_path: string; state: string }[],
  b: readonly { workflow_name: string; definition_path: string; state: string }[]
) {
  const right = new Map(b.map(item => [item.workflow_name, `${item.definition_path}\0${item.state}`]))

  return (
    right.size === b.length &&
    new Set(b.map(item => item.definition_path)).size === b.length &&
    a.length === b.length &&
    new Set(a.map(item => item.workflow_name)).size === a.length &&
    new Set(a.map(item => item.definition_path)).size === a.length &&
    a.every(item => right.get(item.workflow_name) === `${item.definition_path}\0${item.state}`)
  )
}

function samePaths(inventory: readonly WorkflowInventoryItem[], paths: readonly string[]) {
  return (
    new Set(paths).size === paths.length &&
    inventory.length === paths.length &&
    inventory.every(item => paths.includes(item.definition_path))
  )
}

function validInstalledState(state: PackageState | null, identity: PackageIdentity): state is PackageState {
  return Boolean(
    state &&
    !state.busy &&
    state.recovery === 'clear' &&
    state.state === 'installed' &&
    state.installed &&
    state.trust &&
    !state.installed.orphaned_source &&
    sameLifecycleIdentity(state.identity, identity)
  )
}

function validReview(
  operation: LifecycleOperation | null | undefined,
  selection: TrustSelection,
  state: PackageState | null,
  identity: PackageIdentity
): TrustReviewProjection | null {
  if (
    !operation ||
    operation.state !== 'succeeded' ||
    operation.kind !== 'trust_prepare' ||
    operation.subject.type !== 'package' ||
    !sameLifecycleIdentity(operation.subject.identity, identity) ||
    !sameLifecycleValue(operation.selection, selection) ||
    operation.result?.type !== 'trust_review' ||
    !validInstalledState(state, identity)
  ) {
    return null
  }

  const review = operation.result.value
  const installed = state.installed!
  const trust = state.trust!
  const inventory = trust.workflows.map(({ definition_path, workflow_name }) => ({ definition_path, workflow_name }))
  const reviewed = review.workflows.map(({ definition_path, workflow_name }) => ({ definition_path, workflow_name }))

  const selected =
    selection.type === 'all'
      ? review.package_workflows
      : review.package_workflows.filter(item => item.workflow_name === selection.workflow_name)

  if (
    !sameLifecycleIdentity(review.identity, identity) ||
    review.distribution_digest !== installed.distribution_digest ||
    review.distribution_digest !== trust.distribution_digest ||
    review.version !== installed.version ||
    review.resolved_commit !== installed.resolved_commit ||
    !sameInventory(review.package_workflows, inventory) ||
    !samePaths(review.package_workflows, installed.workflow_paths) ||
    !sameInventory(reviewed, selected) ||
    (selection.type === 'one' && selected.length !== 1)
  ) {
    return null
  }

  return review
}

function successfulGrant(
  operation: LifecycleOperation | null | undefined,
  preparation: LifecycleOperation | undefined,
  selection: TrustSelection,
  state: PackageState | null,
  identity: PackageIdentity
) {
  const review = validReview(preparation, selection, state, identity)

  if (
    !review ||
    !operation ||
    operation.state !== 'succeeded' ||
    operation.kind !== 'trust_confirm' ||
    operation.subject.type !== 'package' ||
    !sameLifecycleIdentity(operation.subject.identity, identity) ||
    !sameLifecycleValue(operation.selection, selection) ||
    operation.result?.type !== 'trust_grant' ||
    operation.outcome?.type !== 'committed' ||
    !validInstalledState(state, identity)
  ) {
    return null
  }

  const result = operation.result.value
  const inventory = review.package_workflows

  const actualInventory = result.workflows.map(({ definition_path, workflow_name }) => ({
    definition_path,
    workflow_name
  }))

  if (
    !sameLifecycleIdentity(result.identity, identity) ||
    !sameLifecycleValue(result.selection, selection) ||
    result.distribution_digest !== review.distribution_digest ||
    !sameInventory(actualInventory, inventory) ||
    !sameTrustMap(result.workflows, state.trust!.workflows)
  ) {
    return null
  }

  const selected =
    selection.type === 'all'
      ? result.workflows
      : result.workflows.filter(workflow => workflow.workflow_name === selection.workflow_name)

  return selected.length === (selection.type === 'all' ? inventory.length : 1) &&
    selected.every(workflow => workflow.state === 'trusted')
    ? result
    : null
}

/** Dialog-owned trust selection and token only; admission, polling and barriers stay application-scoped. */
export function usePackageTrustReview({
  binding,
  identity,
  projection,
  projectionEligible,
  focusFallbackRef
}: PackageTrustReviewProps) {
  const supervisor = useMarketplaceSupervisor()
  const queryClient = useQueryClient()
  const records = useSyncExternalStore(supervisor.$records.subscribe, supervisor.$records.get)
  useSyncExternalStore(supervisor.reconciliation.$revision.subscribe, supervisor.reconciliation.$revision.get)
  const [attachment, setAttachment] = useState<TrustAttachment | null>(null)
  const owner = useRef<TrustAttachment | null>(null)
  const origin = useRef<HTMLButtonElement | null>(null)
  const secret = useRef<string | null>(null)
  const confirming = useRef(false)
  const refreshing = useRef<object | null>(null)
  const [refreshPending, setRefreshPending] = useState(false)
  const latest = useRef({ binding, identity, projection, projectionEligible })
  const reconciled = useRef(new Set<string>())

  useLayoutEffect(() => {
    latest.current = { binding, identity, projection, projectionEligible }
  }, [binding, identity, projection, projectionEligible])

  const gate = supervisor.getPackageGate(binding, identity, projection)
  const supported = supervisor.supports(binding, ['operations', 'admission_replay', 'package_state', 'trust'])

  const ready =
    supported && gate.state === 'ready' && validInstalledState(gate.packageState, identity) && !refreshPending

  const record = attachment?.requestId
    ? records.find(value => value.requestId === attachment.requestId && sameLifecycleValue(value.binding, binding))
    : undefined

  useEffect(() => {
    void supervisor.reconcilePackage(binding, identity)
  }, [binding, identity, supervisor])

  useEffect(
    () => () => {
      owner.current = null
      secret.current = null
      confirming.current = false
      refreshing.current = null
    },
    []
  )

  useEffect(() => {
    for (const value of records) {
      if (
        !sameLifecycleValue(value.binding, binding) ||
        !value.kind.startsWith('trust_') ||
        value.subject.type !== 'package' ||
        !sameLifecycleIdentity(value.subject.identity, identity) ||
        (value.status !== 'terminal' && value.status !== 'evicted' && !value.admissionWindowClosed)
      ) {
        continue
      }

      const stamp = value.requestId + ':' + value.status + ':' + value.admissionWindowClosed

      if (!reconciled.current.has(stamp)) {
        reconciled.current.add(stamp)
        void supervisor.reconcilePackage(binding, identity)
      }
    }
  }, [binding, identity, records, supervisor])

  const actionAllowed = useCallback(() => {
    const current = supervisor.getPackageGate(binding, identity, projection)

    return (
      projectionEligible &&
      current.state === 'ready' &&
      supervisor.supports(binding, ['operations', 'admission_replay', 'package_state', 'trust']) &&
      validInstalledState(current.packageState, identity)
    )
  }, [binding, identity, projection, projectionEligible, supervisor])

  const withAuthority = useCallback(
    async (execute: () => void | Promise<void>) => {
      if (refreshing.current) {
        return
      }

      if (actionAllowed()) {
        await execute()

        return
      }

      if (!ready) {
        return
      }

      const ticket = {}
      const expectedOwner = owner.current
      const expected = latest.current
      const previousInstalled = gate.packageState?.installed

      const projectionFilter = expected.projection
        ? { queryKey: expected.projection, exact: true, type: 'all' as const }
        : null

      const projectionQuery = projectionFilter ? queryClient.getQueryCache().find(projectionFilter) : null

      if (
        !expected.projectionEligible ||
        (projectionFilter && (!projectionQuery || projectionQuery.isDisabled() || projectionQuery.isStatic()))
      ) {
        return
      }

      const projectionOptions = projectionQuery?.options
      refreshing.current = ticket
      setRefreshPending(true)

      const owns = () =>
        refreshing.current === ticket &&
        owner.current === expectedOwner &&
        sameLifecycleValue(latest.current.binding, expected.binding) &&
        sameLifecycleIdentity(latest.current.identity, expected.identity) &&
        sameLifecycleValue(latest.current.projection, expected.projection) &&
        latest.current.projectionEligible === expected.projectionEligible

      try {
        const fresh = await supervisor.reconcileScope({ connectionId: binding.connectionId, profile: binding.profile })

        if (
          !owns() ||
          !sameLifecycleValue(fresh, binding) ||
          !supervisor.supports(binding, ['operations', 'admission_replay', 'package_state', 'trust'])
        ) {
          return
        }

        if (projectionFilter && projectionQuery && projectionOptions) {
          if (queryClient.getQueryCache().find(projectionFilter) !== projectionQuery) {
            return
          }

          try {
            await projectionQuery.fetch(projectionOptions, { cancelRefetch: false })
          } catch {
            return
          }

          if (queryClient.getQueryCache().find(projectionFilter) !== projectionQuery) {
            return
          }
        }

        if (!owns() || !supervisor.bindings.isCurrent(binding)) {
          return
        }

        const state = await supervisor.reconcilePackage(binding, identity)

        if (owns() && state && sameLifecycleValue(state.installed, previousInstalled) && actionAllowed()) {
          await execute()
        }
      } finally {
        if (refreshing.current === ticket) {
          refreshing.current = null
          setRefreshPending(false)
        }
      }
    },
    [actionAllowed, binding, gate.packageState, identity, queryClient, ready, supervisor]
  )

  const dispatch = useCallback(
    async (intent: MarketplaceIntent, current: TrustAttachment) => {
      owner.current = current
      setAttachment(current)

      try {
        const admission = supervisor.start(intent, binding)

        if ('confirmation_token' in intent.body) {
          intent.body.confirmation_token = ''
        }

        const key = await admission

        if (owner.current !== current) {
          return
        }

        const exact = supervisor.$records
          .get()
          .find(value => value.key === key && sameLifecycleValue(value.binding, binding))

        const next = { ...current, requestId: exact?.requestId ?? null, failed: !exact }
        owner.current = next
        setAttachment(next)
      } catch {
        if (owner.current === current) {
          const next = { ...current, failed: true }
          owner.current = next
          setAttachment(next)
        }
      }
    },
    [binding, supervisor]
  )

  const prepare = useCallback(
    (selection: TrustSelection = { type: 'all' }, action?: HTMLButtonElement, inventory = attachment?.inventory) => {
      if (owner.current && action) {
        return
      }

      if (action) {
        origin.current = action
      }

      secret.current = null
      const current: TrustAttachment = { requestId: null, failed: false, selection, inventory: inventory ?? null }

      void withAuthority(() =>
        dispatch(
          {
            kind: 'trust_prepare',
            subject: { type: 'package', identity },
            selection,
            body: { identity, workflow_name: selection.type === 'one' ? selection.workflow_name : null }
          },
          current
        )
      )
    },
    [attachment?.inventory, dispatch, identity, withAuthority]
  )

  const close = useCallback(() => {
    refreshing.current = null
    setRefreshPending(false)
    owner.current = null
    secret.current = null
    setAttachment(null)
  }, [])

  const restoreFocus = useCallback(() => {
    const active = globalThis.document.activeElement

    if (active && active !== globalThis.document.body && active.isConnected) {
      origin.current = null

      return
    }

    const target = origin.current

    if (target?.isConnected && !target.disabled) {
      target.focus()
    } else {
      focusFallbackRef.current?.focus()
    }

    origin.current = null
  }, [focusFallbackRef])

  const preparedReview = validReview(
    record?.operation,
    attachment?.selection ?? { type: 'all' },
    gate.packageState,
    identity
  )

  const inventory = preparedReview?.package_workflows ?? attachment?.inventory ?? []

  const select = useCallback(
    (selection: TrustSelection) => {
      const currentInventory = preparedReview?.package_workflows ?? attachment?.inventory ?? []

      if (selection.type === 'one' && !currentInventory.some(item => item.workflow_name === selection.workflow_name)) {
        return
      }

      if (!sameLifecycleValue(selection, attachment?.selection)) {
        prepare(selection, undefined, currentInventory)
      }
    },
    [attachment?.inventory, attachment?.selection, prepare, preparedReview?.package_workflows]
  )

  const confirm = useCallback(async () => {
    const current = owner.current
    const prepared = record?.operation
    const review = validReview(prepared, current?.selection ?? { type: 'all' }, gate.packageState, identity)

    if (!current || current !== attachment || confirming.current || !review || !review.confirmation_available) {
      return
    }

    confirming.current = true

    await withAuthority(async () => {
      try {
        if (
          !validReview(
            prepared,
            current.selection,
            supervisor.getPackageGate(binding, identity, projection).packageState,
            identity
          )
        ) {
          return
        }

        let token: ReviewTokenResponse | null = await getLifecycleReviewToken(prepared, binding)

        if (
          owner.current !== current ||
          !supervisor.bindings.isCurrent(binding) ||
          !actionAllowed() ||
          !sameLifecycleValue(token.selection, current.selection)
        ) {
          return
        }

        secret.current = token.confirmation_token

        const body = {
          confirmation_token: secret.current,
          prepare_operation_id: prepared!.id,
          subject: prepared!.subject as Extract<LifecycleOperation['subject'], { type: 'package' }>,
          selection: current.selection,
          review_digest: review.review_digest
        }

        token = null
        secret.current = null
        await dispatch(
          {
            kind: 'trust_confirm',
            subject: body.subject,
            selection: current.selection,
            body
          },
          { ...current, requestId: null, preparation: prepared ?? undefined }
        )
      } catch {
        if (owner.current === current && supervisor.bindings.isCurrent(binding)) {
          const next = { ...current, tokenUnavailable: true }
          owner.current = next
          setAttachment(next)
        }
      } finally {
        secret.current = null
      }
    })
    confirming.current = false
  }, [
    actionAllowed,
    attachment,
    binding,
    gate.packageState,
    identity,
    projection,
    record?.operation,
    supervisor,
    withAuthority,
    dispatch
  ])

  const progress =
    attachment && !attachment.failed && (!record || record.status === 'admitting' || record.status === 'watching')

  const success = attachment?.preparation
    ? successfulGrant(record?.operation, attachment.preparation, attachment.selection, gate.packageState, identity)
    : null

  let view: TrustReviewDialogView | null = null

  if (attachment) {
    if (progress) {
      view = {
        kind: 'progress',
        phase: record?.operation?.phase ?? (gate.state === 'reconciling' ? 'reconciling' : 'queued'),
        progress: record?.operation?.progress ?? 0,
        cancellable: Boolean(record?.operationId && record.status !== 'terminal' && !record.callPending)
      }
    } else if (preparedReview && !attachment.tokenUnavailable) {
      view = { kind: 'review', review: preparedReview }
    } else if (success) {
      view = { kind: 'succeeded', selection: attachment.selection, workflows: success.workflows }
    } else if (record?.operation?.outcome?.type === 'cancelled_before_commit') {
      view = { kind: 'cancelled', recoverable: true }
    } else if (record?.status === 'status_unknown' || record?.status === 'admission_unknown') {
      view = { kind: 'status', recoverable: false }
    } else if (record?.status === 'evicted') {
      view = { kind: 'evicted', recoverable: false }
    } else if (attachment.tokenUnavailable) {
      view = { kind: 'stale', recoverable: true }
    } else if (record?.operation?.outcome?.type === 'known_unchanged') {
      view = { kind: 'failed', recoverable: true }
    } else {
      view = { kind: 'unconfirmed', recoverable: false }
    }
  }

  return {
    ready,
    prepare,
    dialog: view ? (
      <TrustReviewDialog
        availableWorkflowNames={inventory.map(item => item.workflow_name)}
        onCancelOperation={() => record && void supervisor.cancel(record.key)}
        onClose={close}
        onGrant={confirm}
        onPrepareAgain={() => prepare(attachment?.selection)}
        onRestoreFocus={restoreFocus}
        onRetryStatus={() => record && void supervisor.retry(record.key)}
        onSelectionChange={select}
        open
        selection={attachment?.selection ?? { type: 'all' }}
        view={view}
      />
    ) : null,
    close
  }
}
