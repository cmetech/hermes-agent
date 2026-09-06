import type { QueryKey } from '@tanstack/react-query'
import { type RefObject, useCallback, useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import { createPortal } from 'react-dom'

import { getLifecycleReviewToken, type LifecycleConnectionBinding } from '@/api/workflow-marketplace-lifecycle'
import { Button } from '@/components/ui/button'
import { useI18n } from '@/i18n'
import { sameLifecycleIdentity, sameLifecycleValue } from '@/lib/workflow-marketplace-lifecycle-codec'
import type { MarketplaceIntent } from '@/store/workflow-marketplace-supervisor'
import type { WorkflowMarketplacePackageDetail } from '@/types/hermes'
import type { LifecycleOperation, PackageIdentity, ReviewTokenResponse } from '@/types/workflow-marketplace-lifecycle'

import { InstallReviewDialog, type InstallReviewDialogView } from './install-review-dialog'
import { packageLifecyclePresentation, unconfirmedPackagePresentation } from './package-lifecycle-presentation'
import { RemoveReviewDialog, type RemoveReviewDialogView } from './remove-review-dialog'
import { useMarketplaceSupervisor } from './supervisor-provider'

type Mode = 'install' | 'update' | 'remove'
interface Attachment {
  mode: Mode
  requestId: string | null
  failed: boolean
  preparation?: LifecycleOperation
  tokenUnavailable?: boolean
}
interface PackageLifecycleProps {
  binding: LifecycleConnectionBinding
  identity: PackageIdentity
  sourceName: string
  detail?: WorkflowMarketplacePackageDetail
  projection?: QueryKey
  focusFallbackRef: RefObject<HTMLElement | null>
  actionContainer?: HTMLElement | null
}

/** Dialog-owned attachment only. Admission, polling and barriers belong to the application. */
export function usePackageLifecycle({
  binding,
  identity,
  projection,
  focusFallbackRef,
  detail
}: PackageLifecycleProps) {
  const supervisor = useMarketplaceSupervisor()
  const records = useSyncExternalStore(supervisor.$records.subscribe, supervisor.$records.get)
  useSyncExternalStore(supervisor.reconciliation.$revision.subscribe, supervisor.reconciliation.$revision.get)
  const [attachment, setAttachment] = useState<Attachment | null>(null)
  const owner = useRef<Attachment | null>(null)
  const origin = useRef<HTMLButtonElement | null>(null)
  const secret = useRef<string | null>(null)
  const confirming = useRef(false)
  const refreshing = useRef<object | null>(null)
  const [refreshPending, setRefreshPending] = useState(false)
  const latest = useRef({ binding, identity, detail, projection })
  useLayoutEffect(() => {
    latest.current = { binding, identity, detail, projection }
  }, [binding, identity, detail, projection])
  const advancedChecks = useRef(new Set<string>())
  const reconciled = useRef(new Set<string>())
  const gate = supervisor.getPackageGate(binding, identity, projection)

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
        value.subject.type !== 'package' ||
        !sameLifecycleIdentity(value.subject.identity, identity)
      ) {
        continue
      }

      if (value.status !== 'terminal' && value.status !== 'evicted' && !value.admissionWindowClosed) {
        continue
      }

      const stamp = value.requestId + ':' + value.status + ':' + value.admissionWindowClosed

      if (reconciled.current.has(stamp)) {
        continue
      }

      reconciled.current.add(stamp)
      void supervisor.reconcilePackage(binding, identity)
    }
  }, [binding, identity, records, supervisor])

  const dispatch = useCallback(
    async (intent: MarketplaceIntent, current: Attachment) => {
      owner.current = current
      setAttachment(current)

      try {
        const admission = supervisor.start(intent, binding)

        // start synchronously copies the validated body into its private, bounded admission closure.
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
        if (owner.current !== current) {
          return
        }

        const next = { ...current, failed: true }
        owner.current = next
        setAttachment(next)
      }
    },
    [binding, supervisor]
  )

  const supported = supervisor.supports(binding, ['operations', 'admission_replay', 'package_state', 'transactions'])
  const ready = supported && gate.state === 'ready' && !refreshPending
  const updates = supervisor.supports(binding, ['updates'])

  const actionAllowed = useCallback(
    (mode: Mode) => {
      const current = supervisor.getPackageGate(binding, identity, projection)

      if (
        current.state !== 'ready' ||
        !current.packageState ||
        !supervisor.supports(binding, ['operations', 'admission_replay', 'package_state', 'transactions'])
      ) {
        return false
      }

      if (mode === 'install') {
        return current.packageState.state === 'absent' && Boolean(detail && !detail.blockers.length)
      }

      if (!current.packageState.installed) {
        return false
      }

      return (
        mode === 'remove' ||
        (!current.packageState.installed.orphaned_source && supervisor.supports(binding, ['updates']))
      )
    },
    [binding, detail, identity, projection, supervisor]
  )

  const withAuthority = useCallback(
    async (mode: Mode, execute: () => void | Promise<void>) => {
      if (refreshing.current) {
        return
      }

      if (actionAllowed(mode)) {
        await execute()

        return
      }

      // Only recover an intent initiated from the previously ready presentation.
      if (!ready || (mode === 'update' && !updates)) {
        return
      }

      const ticket = {}
      const expectedOwner = owner.current
      const expected = latest.current
      const previousInstalled = gate.packageState?.installed
      refreshing.current = ticket
      setRefreshPending(true)

      const owns = () =>
        refreshing.current === ticket &&
        owner.current === expectedOwner &&
        sameLifecycleValue(latest.current.binding, expected.binding) &&
        sameLifecycleIdentity(latest.current.identity, expected.identity) &&
        sameLifecycleValue(latest.current.detail, expected.detail) &&
        sameLifecycleValue(latest.current.projection, expected.projection)

      try {
        const fresh = await supervisor.reconcileScope({ connectionId: binding.connectionId, profile: binding.profile })

        if (
          !owns() ||
          !sameLifecycleValue(fresh, binding) ||
          !supervisor.supports(binding, ['operations', 'admission_replay', 'package_state', 'transactions']) ||
          (mode === 'update' && !supervisor.supports(binding, ['updates']))
        ) {
          return
        }

        const state = await supervisor.reconcilePackage(binding, identity)

        if (owns() && state && sameLifecycleValue(state.installed, previousInstalled) && actionAllowed(mode)) {
          await execute()
        }
      } finally {
        if (refreshing.current === ticket) {
          refreshing.current = null
          setRefreshPending(false)
        }
      }
    },
    [actionAllowed, binding, gate.packageState, identity, ready, supervisor, updates]
  )

  function check(action?: HTMLButtonElement) {
    if (owner.current && action) {
      return
    }

    if (action) {
      origin.current = action
    }

    secret.current = null
    void withAuthority('update', () =>
      dispatch(
        { kind: 'update_check', subject: { type: 'package', identity }, selection: null, body: { identity } },
        { mode: 'update', requestId: null, failed: false }
      )
    )
  }

  const prepare = useCallback(
    (mode: Mode, action?: HTMLButtonElement) => {
      if (owner.current && action) {
        return
      }

      if (action) {
        origin.current = action
      }

      secret.current = null
      const subject = { type: 'package' as const, identity }
      const current = { mode, requestId: null, failed: false }

      if (mode === 'install') {
        void withAuthority(mode, () =>
          dispatch(
            {
              kind: 'install_prepare',
              subject,
              selection: null,
              body: { identifier: identity.source_key + '/' + identity.package_id, ref: null, package_path: null }
            },
            current
          )
        )
      } else {
        void withAuthority(mode, () =>
          dispatch(
            {
              kind: mode === 'remove' ? 'remove_prepare' : 'update_prepare',
              subject,
              selection: null,
              body: { identity }
            },
            current
          )
        )
      }
    },
    [dispatch, identity, withAuthority]
  )

  function close() {
    refreshing.current = null
    setRefreshPending(false)
    owner.current = null
    secret.current = null
    setAttachment(null)
  }

  function restoreFocus() {
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
  }

  const presentation = attachment?.tokenUnavailable
    ? {
        ...unconfirmedPackagePresentation,
        message: 'Review is unavailable. Prepare a fresh review.',
        canPrepareAgain: true
      }
    : record
      ? packageLifecyclePresentation(record, attachment?.preparation)
      : unconfirmedPackagePresentation

  useEffect(() => {
    if (
      !record ||
      record.kind !== 'update_check' ||
      presentation.kind !== 'update_available' ||
      !ready ||
      !updates ||
      advancedChecks.current.has(record.requestId)
    ) {
      return
    }

    advancedChecks.current.add(record.requestId)
    prepare('update')
  }, [record, presentation.kind, ready, updates, prepare])

  async function confirm() {
    const current = owner.current
    const prepared = record?.operation

    if (
      !current ||
      current !== attachment ||
      confirming.current ||
      !prepared ||
      record?.status !== 'terminal' ||
      prepared.kind !== current.mode + '_prepare' ||
      prepared.subject.type !== 'package' ||
      !sameLifecycleIdentity(prepared.subject.identity, identity)
    ) {
      return
    }

    const result = prepared.result

    if (
      !result ||
      !('confirmation_available' in result.value) ||
      !result.value.confirmation_available ||
      (current.mode === 'update' && !updates)
    ) {
      return
    }

    confirming.current = true
    const subject = prepared.subject
    const reviewDigest = result.value.review_digest

    await withAuthority(current.mode, async () => {
      try {
        let token: ReviewTokenResponse | null = await getLifecycleReviewToken(prepared, binding)

        if (owner.current !== current || !supervisor.bindings.isCurrent(binding) || !actionAllowed(current.mode)) {
          return
        }

        secret.current = token.confirmation_token

        const body = {
          confirmation_token: secret.current,
          prepare_operation_id: prepared.id,
          subject,
          selection: null,
          review_digest: reviewDigest
        }

        token = null
        secret.current = null
        await dispatch(
          {
            kind:
              current.mode === 'install'
                ? 'install_confirm'
                : current.mode === 'update'
                  ? 'update_confirm'
                  : 'remove_confirm',
            subject,
            selection: null,
            body
          },
          { mode: current.mode, requestId: null, failed: false, preparation: prepared }
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
  }

  const progress =
    attachment && !attachment.failed && (!record || record.status === 'admitting' || record.status === 'watching')

  const canRetry = Boolean(
    record && !record.callPending && ['status_unknown', 'admission_unknown'].includes(record.status)
  )

  const canPrepareAgain = Boolean(ready && (presentation.canPrepareAgain || record?.status === 'evicted'))
  const canRetryCheck = Boolean(ready && presentation.retryAction === 'check')
  let installView: InstallReviewDialogView | null = null
  let removeView: RemoveReviewDialogView | null = null

  if (attachment?.mode === 'remove') {
    removeView = progress
      ? {
          kind: 'progress',
          phase: record?.operation?.phase ?? 'queued',
          progress: record?.operation?.progress ?? 0,
          cancellable: Boolean(record?.operationId && !record.callPending)
        }
      : !attachment.tokenUnavailable &&
          record?.status === 'terminal' &&
          record.operation?.result?.type === 'remove_review'
        ? { kind: 'review', review: record.operation.result.value }
        : { kind: 'terminal', presentation, canRetry, canPrepareAgain }
  } else if (attachment) {
    const mode = attachment.mode
    const result = !attachment.tokenUnavailable && record?.status === 'terminal' ? record.operation?.result : null
    installView = progress
      ? {
          kind: 'progress',
          mode,
          phase: record?.operation?.phase ?? 'queued',
          progress: record?.operation?.progress ?? 0,
          cancellable: Boolean(record?.operationId && !record.callPending)
        }
      : result?.type === 'install_review' && result.value.confirmation_available && mode === 'install'
        ? { kind: 'review', mode, review: result.value }
        : result?.type === 'update_review' &&
            result.value.confirmation_available &&
            result.value.result !== 'unchanged' &&
            mode === 'update'
          ? { kind: 'review', mode, review: result.value }
          : { kind: 'terminal', mode, presentation, canRetry, canPrepareAgain, canRetryCheck }
  }

  return {
    ready,
    canConfirm: Boolean(!refreshPending && attachment && actionAllowed(attachment.mode)),
    updates,
    gate,
    prepare,
    check,
    confirm,
    close,
    restoreFocus,
    installView,
    removeView,
    cancel: () => {
      if (record) {
        void supervisor.cancel(record.key)
      }
    },
    retry: () => {
      if (record) {
        void supervisor.retry(record.key)
      }
    },
    again: () => {
      if (attachment) {
        if (record?.kind === 'update_check') {
          check()
        } else {
          prepare(attachment.mode)
        }
      }
    }
  }
}

/** Mounted only below a strict supervisor and keyed by exact binding plus selected package. */
export function SupervisedPackageActions(
  props: Omit<PackageLifecycleProps, 'binding'> & { binding: LifecycleConnectionBinding | null }
) {
  const [retained, setRetained] = useState(props.binding)

  useEffect(() => {
    if (props.binding) {
      setRetained(props.binding)
    }
  }, [props.binding])
  const binding = props.binding ?? retained

  return binding ? <BoundPackageActions {...props} binding={binding} key={JSON.stringify(binding)} /> : null
}

function BoundPackageActions(props: PackageLifecycleProps) {
  const lifecycle = usePackageLifecycle(props)
  const { t } = useI18n()
  const copy = t.operations
  const installed = lifecycle.gate.packageState?.installed

  const actions =
    lifecycle.gate.state === 'ready' ? (
      <div className="mt-3 flex flex-wrap gap-2">
        {!installed && props.detail && !props.detail.blockers.length ? (
          <Button
            disabled={!lifecycle.ready}
            onClick={event => lifecycle.prepare('install', event.currentTarget)}
            size="sm"
            type="button"
          >
            {copy.workflowMarketplaceInstallPackage}
          </Button>
        ) : null}
        {installed ? (
          <>
            <Button
              disabled={!lifecycle.ready || !lifecycle.updates || installed.orphaned_source}
              onClick={event => lifecycle.check(event.currentTarget)}
              size="sm"
              type="button"
            >
              {copy.workflowMarketplaceUpdatePackage}
            </Button>
            <Button
              disabled={!lifecycle.ready || !lifecycle.updates || installed.orphaned_source}
              onClick={event => lifecycle.check(event.currentTarget)}
              size="sm"
              type="button"
              variant="secondary"
            >
              {copy.workflowMarketplaceCheckUpdates}
            </Button>
            <Button
              disabled={!lifecycle.ready}
              onClick={event => lifecycle.prepare('remove', event.currentTarget)}
              size="sm"
              type="button"
              variant="secondary"
            >
              {copy.workflowMarketplaceRemovePackage}
            </Button>
            <Button disabled size="sm" type="button" variant="secondary">
              {copy.workflowMarketplaceReviewTrustAction}
            </Button>
          </>
        ) : null}
      </div>
    ) : null

  return (
    <>
      {props.actionContainer === undefined
        ? actions
        : props.actionContainer
          ? createPortal(actions, props.actionContainer)
          : null}
      {lifecycle.installView ? (
        <InstallReviewDialog
          confirmDisabled={!lifecycle.canConfirm}
          onCancelOperation={lifecycle.cancel}
          onClose={lifecycle.close}
          onConfirm={lifecycle.confirm}
          onPrepareAgain={lifecycle.again}
          onRestoreFocus={lifecycle.restoreFocus}
          onRetryCheck={() => lifecycle.check()}
          onRetryStatus={lifecycle.retry}
          onReviewTrust={() => undefined}
          open
          view={lifecycle.installView}
        />
      ) : null}
      {lifecycle.removeView ? (
        <RemoveReviewDialog
          confirmDisabled={!lifecycle.canConfirm}
          onCancelOperation={lifecycle.cancel}
          onClose={lifecycle.close}
          onConfirm={lifecycle.confirm}
          onPrepareAgain={lifecycle.again}
          onRestoreFocus={lifecycle.restoreFocus}
          onRetryStatus={lifecycle.retry}
          open
          sourceName={props.sourceName}
          view={lifecycle.removeView}
        />
      ) : null}
    </>
  )
}
