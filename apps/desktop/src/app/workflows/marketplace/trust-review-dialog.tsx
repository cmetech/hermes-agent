import { useEffect, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle
} from '@/components/ui/dialog'
import { useI18n } from '@/i18n'
import type {
  AllTrustSelection,
  OneTrustSelection,
  TrustReviewProjection,
  TrustWorkflowState
} from '@/types/workflow-marketplace-lifecycle'

import { ReviewFacts, ReviewValueList, WorkflowRiskReview } from './review-sections'

export type TrustSelection = AllTrustSelection | OneTrustSelection

export type TrustReviewDialogView =
  | { kind: 'progress'; phase: string; progress: number; cancellable: boolean }
  | { kind: 'review'; review: TrustReviewProjection }
  | { kind: 'succeeded'; selection: TrustSelection; workflows: readonly TrustWorkflowState[] }
  | { kind: 'cancelled' | 'evicted' | 'failed' | 'stale' | 'status' | 'unconfirmed'; recoverable?: boolean }

export interface TrustReviewDialogProps {
  availableWorkflowNames?: readonly string[]
  onCancelOperation: () => void
  onClose: () => void
  onGrant: () => Promise<void>
  onPrepareAgain: () => void
  onRestoreFocus?: () => void
  onRetryStatus?: () => void
  onSelectionChange: (selection: TrustSelection) => void
  open: boolean
  selection: TrustSelection
  view: TrustReviewDialogView
}

export function TrustReviewDialog({
  availableWorkflowNames,
  onCancelOperation,
  onClose,
  onGrant,
  onPrepareAgain,
  onRestoreFocus,
  onRetryStatus,
  onSelectionChange,
  open,
  selection,
  view
}: TrustReviewDialogProps) {
  const { t } = useI18n()
  const copy = t.operations
  const grantGuardRef = useRef(false)
  const mountedRef = useRef(true)
  const primaryRef = useRef<HTMLButtonElement>(null)
  const [admissionBusy, setAdmissionBusy] = useState(false)
  const review = view.kind === 'review' ? view.review : null
  const workflowNames = availableWorkflowNames ?? review?.workflows.map(workflow => workflow.workflow_name) ?? []
  const selectedWorkflow = selection.type === 'one' ? selection.workflow_name : (workflowNames[0] ?? '')

  // eslint-disable-next-line no-restricted-syntax -- tracks component lifetime for guarded async admission cleanup
  useEffect(() => {
    mountedRef.current = true

    return () => {
      mountedRef.current = false
      grantGuardRef.current = false
    }
  }, [])

  const grant = async () => {
    if (review === null || grantGuardRef.current) {
      return
    }

    grantGuardRef.current = true
    setAdmissionBusy(true)

    try {
      await onGrant()
    } finally {
      grantGuardRef.current = false

      if (mountedRef.current) {
        setAdmissionBusy(false)
      }
    }
  }

  const close = () => {
    if (!admissionBusy) {
      onClose()
    }
  }

  return (
    <Dialog onOpenChange={value => !value && close()} open={open}>
      <DialogContent
        className="w-[min(92vw,54rem)] max-w-4xl"
        onCloseAutoFocus={event => {
          if (onRestoreFocus) {
            event.preventDefault()
            onRestoreFocus()
          }
        }}
        onEscapeKeyDown={event => admissionBusy && event.preventDefault()}
        onOpenAutoFocus={event => {
          event.preventDefault()
          primaryRef.current?.focus()
        }}
        showCloseButton={!admissionBusy}
      >
        <DialogHeader>
          <DialogTitle>
            {view.kind === 'progress' ? copy.workflowMarketplacePreparingTrust : copy.workflowMarketplaceReviewTrust}
          </DialogTitle>
          <DialogDescription>{copy.workflowMarketplaceReviewTrustDescription}</DialogDescription>
        </DialogHeader>

        {view.kind === 'progress' ? (
          <p aria-live="polite" role="status">
            {copy.workflowMarketplaceOperationProgress(view.phase, view.progress)}
          </p>
        ) : view.kind === 'review' ? (
          <>
            <ReviewFacts
              items={[
                [
                  copy.workflowMarketplaceInstalledIdentity,
                  `${view.review.identity.source_key}/${view.review.identity.package_id}`
                ],
                [copy.workflowMarketplaceSource, view.review.source_name],
                [copy.workflowMarketplaceInstalledVersionLabel, view.review.version],
                [copy.workflowMarketplaceCommit, view.review.resolved_commit],
                [copy.workflowMarketplaceDistributionDigest, view.review.distribution_digest],
                [copy.workflowMarketplaceReviewDigest, view.review.review_digest]
              ]}
            />
            <section>
              <h3 className="text-xs font-medium text-(--ui-text-primary)">
                {copy.workflowMarketplacePackageResources}
              </h3>
              <ReviewValueList values={view.review.package_resources} />
            </section>
            <fieldset className="grid gap-2 rounded-md border border-(--ui-stroke-tertiary) p-3">
              <legend className="px-1 text-xs font-medium">{copy.workflowMarketplaceReviewTrust}</legend>
              <label className="flex items-center gap-2 text-xs">
                <input
                  checked={selection.type === 'all'}
                  name="workflow-trust-selection"
                  onChange={() => onSelectionChange({ type: 'all' })}
                  type="radio"
                />
                {copy.workflowMarketplaceTrustAllWorkflows}
              </label>
              <label className="flex items-center gap-2 text-xs">
                <input
                  checked={selection.type === 'one'}
                  disabled={workflowNames.length === 0}
                  name="workflow-trust-selection"
                  onChange={() => {
                    if (workflowNames[0]) {
                      onSelectionChange({ type: 'one', workflow_name: workflowNames[0] })
                    }
                  }}
                  type="radio"
                />
                {copy.workflowMarketplaceTrustOneWorkflow}
              </label>
              {selection.type === 'one' ? (
                <label className="grid gap-1 text-xs">
                  {copy.workflowMarketplaceSelectWorkflow}
                  <select
                    className="rounded-md border border-(--ui-stroke-tertiary) bg-transparent px-2 py-1"
                    onChange={event => onSelectionChange({ type: 'one', workflow_name: event.target.value })}
                    value={selectedWorkflow}
                  >
                    {workflowNames.map(name => (
                      <option key={name} value={name}>
                        {name}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
            </fieldset>
            <section className="grid gap-2">
              <h3 className="text-xs font-medium text-(--ui-text-primary)">{copy.workflowMarketplaceWorkflowRisks}</h3>
              {view.review.workflows.map(workflow => (
                <WorkflowRiskReview key={workflow.workflow_name} showTrustState workflow={workflow} />
              ))}
            </section>
          </>
        ) : view.kind === 'succeeded' ? (
          <>
            <p aria-live="polite" role="status">
              {view.selection.type === 'one'
                ? `Trust granted for ${view.selection.workflow_name}.`
                : 'Trust granted for all reviewed workflows.'}
            </p>
            <section aria-label="Current package trust" role="region">
              <h3 className="text-xs font-medium">Current package trust</h3>
              <ul className="mt-1 space-y-1 text-xs">
                {view.workflows.map(workflow => (
                  <li className="flex justify-between gap-2" key={workflow.workflow_name}>
                    <span>{workflow.workflow_name}</span>
                    <span>{workflow.state === 'trusted' ? copy.workflowTrusted : copy.workflowUntrusted}</span>
                  </li>
                ))}
              </ul>
            </section>
          </>
        ) : (
          <div role="alert">
            {view.kind === 'unconfirmed' || view.kind === 'evicted' || view.kind === 'status' ? (
              'State could not be confirmed. Refresh package state before trying again.'
            ) : view.kind === 'stale' ? (
              copy.workflowMarketplaceTrustNotGranted
            ) : (
              <>
                {copy.workflowMarketplaceTrustNotGranted}
                {view.kind === 'cancelled' ? ` ${copy.workflowMarketplaceCancelled}` : null}
              </>
            )}
          </div>
        )}

        <DialogFooter>
          <Button disabled={admissionBusy} onClick={close} ref={primaryRef} type="button" variant="secondary">
            {t.common.close}
          </Button>
          {view.kind === 'progress' && view.cancellable ? (
            <Button onClick={onCancelOperation} type="button" variant="secondary">
              {copy.workflowMarketplaceCancelOperation}
            </Button>
          ) : null}
          {view.kind === 'review' ? (
            <Button disabled={admissionBusy} onClick={() => void grant()} type="button">
              {copy.workflowMarketplaceGrantTrust}
            </Button>
          ) : null}
          {view.kind === 'status' && onRetryStatus ? (
            <Button onClick={onRetryStatus} type="button">
              {copy.workflowMarketplaceRetryStatus}
            </Button>
          ) : (view.kind === 'stale' ||
              view.kind === 'evicted' ||
              view.kind === 'failed' ||
              view.kind === 'cancelled') &&
            view.recoverable !== false ? (
            <Button onClick={onPrepareAgain} type="button">
              {copy.workflowMarketplacePrepareAgain}
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
