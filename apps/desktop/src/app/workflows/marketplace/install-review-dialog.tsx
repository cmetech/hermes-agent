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
import { ExternalLink } from '@/lib/external-link'
import type { WorkflowMarketplaceInstallReview, WorkflowMarketplaceUpdateReview } from '@/types/hermes'

import { marketplaceFileChangeLabel, marketplacePhaseLabel, marketplaceSeverityLabel } from './controlled-copy'
import { claimLifecycleEscape, useLifecycleDialogFocus } from './lifecycle-dialog-behavior'
import { marketplaceWebRepositoryHref } from './package-detail'
import type { PackageLifecyclePresentation } from './package-lifecycle-presentation'
import {
  ReviewDiagnostics,
  ReviewFacts,
  ReviewRequirements,
  ReviewValueList,
  WorkflowRiskReview
} from './review-sections'

type InstallMode = 'install' | 'update'
type InstallReviewPresentation = Omit<WorkflowMarketplaceInstallReview, 'confirmation_token'>
type UpdateReviewPresentation = Omit<WorkflowMarketplaceUpdateReview, 'confirmation_token'>

export type InstallReviewDialogView =
  | {
      kind: 'terminal'
      mode: InstallMode
      presentation: PackageLifecyclePresentation
      canPrepareAgain: boolean
      canRetryCheck: boolean
      canRetry: boolean
    }
  | { kind: 'progress'; mode: InstallMode; phase: string; progress: number; cancellable: boolean }
  | { kind: 'review'; mode: 'install'; review: InstallReviewPresentation }
  | { kind: 'review'; mode: 'update'; review: UpdateReviewPresentation }
  | { kind: 'unchanged'; mode: 'update'; version: string }
  | { kind: 'succeeded'; mode: InstallMode; version: string; trustRequired: boolean }
  | {
      kind: 'cancelled' | 'evicted' | 'failed' | 'stale' | 'status'
      mode: InstallMode
      previousVersion?: string
      recoverable: boolean
    }

export interface InstallReviewDialogProps {
  confirmDisabled?: boolean
  onCancelOperation: () => void
  onClose: () => void
  onRestoreFocus?: () => void
  onConfirm: () => Promise<void>
  onPrepareAgain: () => void
  onRetryCheck?: () => void
  onRetryStatus?: () => void
  onReviewTrust: () => void
  reviewTrustDisabled?: boolean
  open: boolean
  view: InstallReviewDialogView
}

function ChangeSets({ review }: { review: UpdateReviewPresentation }) {
  const { t } = useI18n()
  const copy = t.operations

  const requirementGroups = [
    [copy.workflowMarketplaceProviders, review.requirement_changes.providers],
    [copy.workflowMarketplaceRuntimes, review.requirement_changes.runtimes],
    [copy.workflowMarketplaceSecrets, review.requirement_changes.secrets],
    [copy.workflowMarketplaceServices, review.requirement_changes.services],
    [copy.workflowMarketplaceTools, review.requirement_changes.tools]
  ] as const

  const workflowChanges = [
    ...review.workflow_changes.added.map(value => [copy.workflowMarketplaceAdded, value] as const),
    ...review.workflow_changes.removed.map(value => [copy.workflowMarketplaceRemoved, value] as const)
  ]

  return (
    <div className="grid gap-3 sm:grid-cols-2">
      <section>
        <h3 className="text-xs font-medium text-(--ui-text-primary)">{copy.workflowMarketplaceWorkflowChanges}</h3>
        {workflowChanges.length ? (
          <ul className="mt-1 space-y-1 text-xs text-(--ui-text-secondary)">
            {workflowChanges.map(([kind, value]) => (
              <li key={`${kind}:${value}`}>
                <span>{kind}: </span>
                <code>{value}</code>
              </li>
            ))}
          </ul>
        ) : (
          <ReviewValueList empty values={[]} />
        )}
      </section>
      <section>
        <h3 className="text-xs font-medium text-(--ui-text-primary)">{copy.workflowMarketplaceRiskChanges}</h3>
        <ReviewValueList
          empty
          values={[
            ...review.risk_changes.added.map(
              value =>
                `${copy.workflowMarketplaceAdded}: ${value.workflow_name} ${value.package_digest} ${value.risk_digest}`
            ),
            ...review.risk_changes.removed.map(
              value =>
                `${copy.workflowMarketplaceRemoved}: ${value.workflow_name} ${value.package_digest} ${value.risk_digest}`
            )
          ]}
        />
      </section>
      <section>
        <h3 className="text-xs font-medium text-(--ui-text-primary)">{copy.workflowMarketplaceCompatibilityChanges}</h3>
        <ReviewValueList
          empty
          values={[
            ...review.compatibility_changes.added.map(
              value =>
                `${copy.workflowMarketplaceAdded}: ${value.workflow_name} ${value.code} ${marketplaceSeverityLabel(copy, value.severity)}`
            ),
            ...review.compatibility_changes.removed.map(
              value =>
                `${copy.workflowMarketplaceRemoved}: ${value.workflow_name} ${value.code} ${marketplaceSeverityLabel(copy, value.severity)}`
            )
          ]}
        />
      </section>
      <section>
        <h3 className="text-xs font-medium text-(--ui-text-primary)">{copy.workflowMarketplaceRequirementChanges}</h3>
        <ReviewValueList
          empty
          values={requirementGroups.flatMap(([label, changes]) => [
            ...changes.added.map(value => `${copy.workflowMarketplaceAdded} ${label}: ${value}`),
            ...changes.removed.map(value => `${copy.workflowMarketplaceRemoved} ${label}: ${value}`)
          ])}
        />
      </section>
    </div>
  )
}

function ReviewContent({ view }: { view: Extract<InstallReviewDialogView, { kind: 'review' }> }) {
  const { t } = useI18n()
  const copy = t.operations
  const review = view.review
  const repositoryHref = marketplaceWebRepositoryHref(review.repository_url)
  const install = view.mode === 'install' ? view.review : null
  const update = view.mode === 'update' ? view.review : null

  return (
    <>
      <ReviewFacts
        items={[
          [copy.workflowMarketplaceInstalledIdentity, `${review.identity.source_key}/${review.identity.package_id}`],
          [copy.workflowMarketplaceSource, review.source_name],
          [copy.workflowMarketplaceRef, review.configured_ref ?? copy.workflowMarketplaceDefaultRef],
          [copy.workflowMarketplacePackagePath, install?.package_path ?? null],
          [copy.workflowMarketplaceCandidateVersion, review.candidate_version],
          [copy.workflowMarketplaceCandidateCommit, install?.resolved_commit ?? update?.candidate_commit ?? null],
          [copy.workflowMarketplaceCandidateDigest, review.candidate_digest],
          [copy.workflowMarketplaceReviewDigest, review.review_digest]
        ]}
      />
      <section>
        <h3 className="text-xs font-medium text-(--ui-text-primary)">{copy.workflowMarketplaceRepository}</h3>
        <p className="break-all font-mono text-xs">
          {repositoryHref ? (
            <ExternalLink href={repositoryHref}>{review.repository_url}</ExternalLink>
          ) : (
            review.repository_url
          )}
        </p>
      </section>
      {update ? (
        <ReviewFacts
          items={[
            [copy.workflowMarketplacePreviousVersion, update.old_version],
            [copy.workflowMarketplacePreviousCommit, update.old_commit],
            [copy.workflowMarketplacePreviousDigest, update.old_digest]
          ]}
        />
      ) : null}
      <section>
        <h3 className="text-xs font-medium text-(--ui-text-primary)">{copy.workflowMarketplaceChangedFiles}</h3>
        <ul className="mt-1 space-y-1 text-xs text-(--ui-text-secondary)">
          {review.file_changes.map(change => (
            <li key={`${change.kind}:${change.old_path ?? ''}:${change.path}`}>
              <span>{marketplaceFileChangeLabel(copy, change.kind)}: </span>
              <code>{change.path}</code>
              {change.old_path ? (
                <span className="ms-2">
                  {copy.workflowMarketplaceRenamedFrom('')}
                  <code>{change.old_path}</code>
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      </section>
      <section>
        <h3 className="text-xs font-medium text-(--ui-text-primary)">{copy.workflowMarketplacePackageResources}</h3>
        <ReviewValueList values={review.assessment.package_resources} />
      </section>
      <ReviewRequirements requirements={review.assessment.external_requirements} />
      <ReviewDiagnostics diagnostics={review.assessment.blockers} title={copy.workflowMarketplaceBlockers} />
      <ReviewDiagnostics diagnostics={review.assessment.advisories} title={copy.workflowMarketplaceAdvisories} />
      {update ? <ChangeSets review={update} /> : null}
      <section className="grid gap-2">
        <h3 className="text-xs font-medium text-(--ui-text-primary)">{copy.workflowMarketplaceWorkflowRisks}</h3>
        {review.workflow_reviews.map(workflow => (
          <WorkflowRiskReview key={workflow.workflow_name} workflow={workflow} />
        ))}
      </section>
    </>
  )
}

export function InstallReviewDialog({
  confirmDisabled = false,
  onCancelOperation,
  onClose,
  onRestoreFocus,
  onConfirm,
  onPrepareAgain,
  onRetryCheck,
  onRetryStatus,
  onReviewTrust,
  reviewTrustDisabled = true,
  open,
  view
}: InstallReviewDialogProps) {
  const { t } = useI18n()
  const copy = t.operations
  const confirmGuardRef = useRef(false)
  const mountedRef = useRef(true)
  const primaryRef = useRef<HTMLButtonElement>(null)
  const headingRef = useRef<HTMLHeadingElement>(null)
  const [admissionBusy, setAdmissionBusy] = useState(false)
  const rememberDialogFocus = useLifecycleDialogFocus(open, [view.kind, admissionBusy], primaryRef, headingRef)

  // eslint-disable-next-line no-restricted-syntax -- tracks component lifetime for guarded async admission cleanup
  useEffect(() => {
    mountedRef.current = true

    return () => {
      mountedRef.current = false
      confirmGuardRef.current = false
    }
  }, [])

  const mode = view.mode
  const review = view.kind === 'review' ? view.review : null
  const blocked = review?.assessment.blockers.length ? true : false
  const canConfirm = review !== null && !blocked && !confirmDisabled

  const title =
    view.kind === 'review'
      ? mode === 'install'
        ? copy.workflowMarketplaceReviewInstallation
        : copy.workflowMarketplaceReviewUpdate
      : mode === 'install'
        ? copy.workflowMarketplacePreparingInstallation
        : view.kind === 'progress' && view.phase === 'checking'
          ? copy.workflowMarketplaceCheckingUpdates
          : copy.workflowMarketplacePreparingUpdate

  const confirm = async () => {
    if (!canConfirm || confirmGuardRef.current) {
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
        className="w-[min(92vw,54rem)] max-w-4xl motion-reduce:animate-none motion-reduce:transition-none"
        data-marketplace-lifecycle-dialog
        onCloseAutoFocus={event => {
          if (onRestoreFocus) {
            event.preventDefault()
            onRestoreFocus()
          }
        }}
        onEscapeKeyDown={event => event.preventDefault()}
        onFocusCapture={rememberDialogFocus}
        onInteractOutside={event => admissionBusy && event.preventDefault()}
        onKeyDown={event => {
          if (event.key === 'Escape') {
            claimLifecycleEscape(event, !admissionBusy, close)
          }
        }}
        onOpenAutoFocus={event => {
          event.preventDefault()
          primaryRef.current?.focus()
        }}
        showCloseButton={!admissionBusy}
      >
        <DialogHeader>
          <DialogTitle ref={headingRef} tabIndex={-1}>
            {title}
          </DialogTitle>
          <DialogDescription>
            {mode === 'install'
              ? copy.workflowMarketplaceReviewInstallationDescription
              : copy.workflowMarketplaceReviewUpdateDescription}
          </DialogDescription>
        </DialogHeader>

        {view.kind === 'progress' ? (
          <p role="status">
            {copy.workflowMarketplaceOperationProgress(marketplacePhaseLabel(copy, view.phase), view.progress)}
          </p>
        ) : view.kind === 'review' ? (
          <ReviewContent view={view} />
        ) : view.kind === 'succeeded' ? (
          <p role="status">
            {view.mode === 'install'
              ? copy.workflowMarketplaceInstalledTrustRequired
              : copy.workflowMarketplaceUpdatedTrustRequired(view.version)}
          </p>
        ) : view.kind === 'unchanged' ? (
          <p role="status">{copy.workflowMarketplacePackageCurrent(view.version)}</p>
        ) : view.kind === 'terminal' ? (
          <p
            role={
              view.presentation.kind === 'unconfirmed' || view.presentation.kind === 'check_error' ? 'alert' : 'status'
            }
          >
            {view.presentation.message}
          </p>
        ) : (
          <div role="alert">
            <p>{copy.workflowMarketplaceUnconfirmed}</p>
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
            <Button disabled={!canConfirm || admissionBusy} onClick={() => void confirm()} type="button">
              {view.mode === 'install' ? copy.workflowMarketplaceConfirmInstall : copy.workflowMarketplaceConfirmUpdate}
            </Button>
          ) : null}
          {view.kind === 'succeeded' && view.trustRequired ? (
            <Button disabled={reviewTrustDisabled} onClick={onReviewTrust} type="button">
              {copy.workflowMarketplaceReviewTrustAction}
            </Button>
          ) : null}
          {view.kind === 'status' && onRetryStatus ? (
            <Button onClick={onRetryStatus} type="button">
              {copy.workflowMarketplaceRetryStatus}
            </Button>
          ) : view.kind === 'terminal' && view.canRetry && onRetryStatus ? (
            <Button onClick={onRetryStatus} type="button">
              {copy.workflowMarketplaceRetryStatus}
            </Button>
          ) : view.kind === 'terminal' && view.canRetryCheck && onRetryCheck ? (
            <Button onClick={onRetryCheck} type="button">
              {copy.workflowMarketplaceRetryCheck}
            </Button>
          ) : view.kind === 'terminal' && view.canPrepareAgain ? (
            <Button onClick={onPrepareAgain} type="button">
              {copy.workflowMarketplacePrepareAgain}
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
