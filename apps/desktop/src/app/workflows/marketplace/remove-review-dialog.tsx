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
import type { WorkflowMarketplaceRemoveReview } from '@/types/hermes'

import { ReviewFacts, ReviewValueList } from './review-sections'

export type RemoveReviewDialogView =
  | { kind: 'progress'; phase: string; progress: number; cancellable: boolean }
  | { kind: 'review'; review: Omit<WorkflowMarketplaceRemoveReview, 'confirmation_token'> }
  | { kind: 'succeeded' }
  | { kind: 'cancelled' | 'evicted' | 'failed' | 'stale' | 'status'; currentVersion: string; recoverable?: boolean }

export interface RemoveReviewDialogProps {
  onCancelOperation: () => void
  onClose: () => void
  onConfirm: () => Promise<void>
  onPrepareAgain: () => void
  onRetryStatus?: () => void
  open: boolean
  sourceName: string
  view: RemoveReviewDialogView
}

export function RemoveReviewDialog({
  onCancelOperation,
  onClose,
  onConfirm,
  onPrepareAgain,
  onRetryStatus,
  open,
  sourceName,
  view
}: RemoveReviewDialogProps) {
  const { t } = useI18n()
  const copy = t.operations
  const confirmGuardRef = useRef(false)
  const mountedRef = useRef(true)
  const primaryRef = useRef<HTMLButtonElement>(null)
  const [admissionBusy, setAdmissionBusy] = useState(false)

  // eslint-disable-next-line no-restricted-syntax -- tracks component lifetime for guarded async admission cleanup
  useEffect(() => {
    mountedRef.current = true

    return () => {
      mountedRef.current = false
      confirmGuardRef.current = false
    }
  }, [])

  const confirm = async () => {
    if (view.kind !== 'review' || confirmGuardRef.current) {
      return
    }

    confirmGuardRef.current = true
    setAdmissionBusy(true)

    try {
      await onConfirm()
    } finally {
      confirmGuardRef.current = false

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
        className="w-[min(92vw,42rem)] max-w-2xl"
        onEscapeKeyDown={event => admissionBusy && event.preventDefault()}
        onOpenAutoFocus={event => {
          event.preventDefault()
          primaryRef.current?.focus()
        }}
        showCloseButton={!admissionBusy}
      >
        <DialogHeader>
          <DialogTitle>
            {view.kind === 'progress'
              ? copy.workflowMarketplacePreparingRemoval
              : copy.workflowMarketplaceReviewRemoval}
          </DialogTitle>
          <DialogDescription>{copy.workflowMarketplaceReviewRemovalDescription}</DialogDescription>
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
                [copy.workflowMarketplaceSource, sourceName],
                [copy.workflowMarketplaceInstalledVersionLabel, view.review.current_version],
                [copy.workflowMarketplaceCommit, view.review.current_commit],
                [copy.workflowMarketplaceDistributionDigest, view.review.distribution_digest],
                [copy.workflowMarketplaceReviewDigest, view.review.review_digest]
              ]}
            />
            <section>
              <h3 className="text-xs font-medium text-(--ui-text-primary)">
                {copy.workflowMarketplaceWorkflowCount(view.review.workflow_names.length)}
              </h3>
              <ReviewValueList values={view.review.workflow_names} />
            </section>
            <div className="grid gap-1 rounded-md border border-amber-500/30 bg-amber-500/10 p-3 text-xs">
              <p>{copy.workflowMarketplaceRemovalScope}</p>
              <p>{copy.workflowMarketplaceRemovalPreserves}</p>
            </div>
          </>
        ) : view.kind === 'succeeded' ? (
          <p aria-live="polite" role="status">
            {copy.workflowMarketplacePackageRemoved}
          </p>
        ) : (
          <div role="alert">
            <p>
              {view.kind === 'stale'
                ? copy.workflowMarketplaceReviewStale
                : view.kind === 'evicted'
                  ? copy.workflowMarketplaceOperationStatusLost
                  : view.kind === 'status'
                    ? copy.workflowMarketplaceOperationStatusUnavailable
                    : view.kind === 'cancelled'
                      ? copy.workflowMarketplaceRemovalCancelled
                      : copy.workflowMarketplaceRemovalFailed}
            </p>
            <p>{copy.workflowMarketplaceVersionRemainsInstalled(view.currentVersion)}</p>
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
            <Button disabled={admissionBusy} onClick={() => void confirm()} type="button" variant="destructive">
              {copy.workflowMarketplaceConfirmRemoval}
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
